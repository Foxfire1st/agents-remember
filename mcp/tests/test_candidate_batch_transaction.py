"""Focused behaviour of the candidate batch's transaction boundary and its admission rules.

Each case protects one consequential failure: a late-invalid batch whose earlier inserts must be
gone, a competing writer whose batch must be refused rather than rebased, a payload that tries to
carry its own authority, a lane that is not a candidate, and an expectation that no longer holds.
Every refusal case measures the stored dataset before and after, because "nothing was written" is
the property the whole operation exists to provide.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from uuid import uuid4

import pytest
from agents_remember.memory.knowledge import anchors, lineage, memberships, realizations
from agents_remember.memory.knowledge.batch_preconditions import require_preconditions
from agents_remember.memory.knowledge.lineage import LineageEdge
from agents_remember.memory.knowledge.refusals import KnowledgeRefused
from agents_remember.models.knowledge.candidate import (
    AddFamily,
    AddFamilyMember,
    AddFamilyRevision,
    AddInvariant,
    AddInvariantRevision,
    ChangeBatch,
    ChangeCommand,
    ExpectedRecord,
    RemoveRealizationClaim,
    SetInvariantLabel,
)
from agents_remember.models.knowledge.family import FamilyRevisionDraft
from agents_remember.models.knowledge.graph import (
    FamilyMember,
    FamilyMemberDraft,
)
from agents_remember.models.knowledge.result import RevisionDraft
from candidate_batch_test_support import (
    CandidateHarness,
    CommandSeeds,
    RefusalEvidence,
    build_candidate_harness,
    claim_command,
    family_revision_draft,
    insert_raw_membership,
    invariant_digest,
    measure_refusal,
    record_is_gone,
    removal_seeds,
    revision_draft,
)

pytestmark = pytest.mark.evidence_unit


@pytest.fixture
def candidate(tmp_path: Path) -> CandidateHarness:
    return build_candidate_harness(tmp_path / "candidate")


def test_a_late_invalid_command_rolls_back_every_earlier_insert_in_the_batch(
    candidate: CandidateHarness,
) -> None:
    """A batch whose final command is invalid leaves none of its earlier rows behind.

    This is the operation's central obligation, and the shape of the case is what makes it real: the
    batch inserts an identity, a revision and a second revision successfully, then names a family
    revision that exists nowhere. Without one transaction around the whole batch, the first three
    rows would be committed and the dataset would hold a prefix of a rejected request.
    """

    seeds = CommandSeeds()
    batch = candidate.batch(
        AddInvariant(invariant_id=seeds.invariant_id, display_label="rollback invariant"),
        AddInvariantRevision(
            revision=revision_draft(
                candidate, invariant_id=seeds.invariant_id, revision_id=seeds.revision_id
            )
        ),
        AddInvariantRevision(
            revision=revision_draft(
                candidate,
                invariant_id=seeds.invariant_id,
                revision_id=seeds.successor_id,
                predecessors=(seeds.revision_id,),
            )
        ),
        AddFamilyMember(
            member=FamilyMemberDraft(
                member_id=seeds.member_id,
                family_revision_id=str(uuid4()),
                invariant_revision_id=seeds.revision_id,
                provenance=candidate.authorship,
            )
        ),
    )

    evidence = measure_refusal(candidate, batch)

    assert evidence.result.state == "refused"
    assert evidence.refusal.code == "invalid_reference"
    assert evidence.refusal.operation == "change_candidate"
    assert "command 3 (add_family_member)" in evidence.refusal.detail
    assert evidence.wrote_nothing()
    assert evidence.counts_after["invariant"] == 0
    assert evidence.counts_after["invariant_revision"] == 0
    assert evidence.counts_after["family_member"] == 0


def test_a_late_constraint_failure_inside_one_batch_rolls_the_whole_batch_back(
    candidate: CandidateHarness,
) -> None:
    """A duplicate relationship discovered by the database also rolls the batch back whole.

    The previous case refuses at a precondition. This one is refused by the database: the batch
    authors a membership for a pair an earlier *batch* already related, which the declared unique
    tuple catches. Both doors lead to the same place, and the rows the batch inserted before that
    point must be gone in both.
    """

    (_, invariant_revision_id) = candidate.seed(1)[0]
    family_seeds = CommandSeeds()
    assert (
        candidate.apply(
            candidate.batch(
                AddFamily(family_id=family_seeds.family_id, display_label="membership family"),
                AddFamilyRevision(
                    revision=FamilyRevisionDraft(
                        family_id=family_seeds.family_id,
                        revision_id=family_seeds.family_revision_id,
                        display_version="v1",
                        joint_guarantee="The authored obligations hold together.",
                        provenance=candidate.authorship,
                    )
                ),
            )
        ).state
        == "changed"
    )
    seeded_member = candidate.seed_membership(
        family_revision_id=family_seeds.family_revision_id,
        invariant_revision_id=invariant_revision_id,
    )
    seeds = CommandSeeds()
    evidence = measure_refusal(
        candidate,
        candidate.batch(
            AddInvariant(
                invariant_id=seeds.invariant_id, display_label="late constraint invariant"
            ),
            AddFamilyMember(
                member=FamilyMemberDraft(
                    member_id=seeds.member_id,
                    family_revision_id=family_seeds.family_revision_id,
                    invariant_revision_id=invariant_revision_id,
                    provenance=candidate.authorship,
                )
            ),
        ),
    )

    assert evidence.result.state == "refused"
    assert evidence.refusal.code == "relationship_constraint"
    assert evidence.wrote_nothing()
    assert evidence.counts_after["invariant"] == 1
    assert evidence.counts_after["family"] == 1
    assert evidence.counts_after["family_member"] == 1
    assert seeded_member is not None


def test_a_competing_writer_leaves_the_second_batch_refused_with_the_state_untouched(
    candidate: CandidateHarness,
) -> None:
    """Two batches authored against one context: only the first may apply.

    The second batch is what a caller that lost the race actually submits -- the same expected
    dataset identity beside its own proposal. It is refused with both identities named, and the
    winner's records are still exactly what they were, because the operation never rebases a batch
    onto a snapshot that arrived while it was being authored.
    """

    (invariant_id, base_revision_id) = candidate.seed(1)[0]
    winning = CommandSeeds()
    stale_context = candidate.context()
    winner = ChangeBatch(
        expected=stale_context,
        commands=(
            AddInvariantRevision(
                revision=revision_draft(
                    candidate,
                    invariant_id=invariant_id,
                    revision_id=winning.revision_id,
                    predecessors=(base_revision_id,),
                )
            ),
        ),
    )
    assert candidate.apply(winner).state == "changed"
    after_winner = candidate.logical_digest()

    loser = ChangeBatch(
        expected=stale_context,
        commands=(
            AddInvariantRevision(
                revision=revision_draft(
                    candidate,
                    invariant_id=invariant_id,
                    revision_id=winning.successor_id,
                    predecessors=(base_revision_id,),
                )
            ),
        ),
    )
    evidence = measure_refusal(candidate, loser)

    assert evidence.result.state == "refused"
    assert evidence.refusal.code == "stale_precondition"
    assert evidence.refusal.expected == stale_context.knowledge.logical_digest
    assert evidence.refusal.observed == after_winner
    assert evidence.wrote_nothing()
    assert evidence.counts_after["invariant_revision"] == 2


def test_an_expected_record_that_changed_refuses_and_leaves_the_row_alone(
    candidate: CandidateHarness,
) -> None:
    """A stated expectation that no longer holds refuses the batch and preserves the stored row."""

    (invariant_id, _) = candidate.seed(1)[0]
    stale_digest = "0" * 64
    batch = ChangeBatch(
        expected=candidate.context(),
        expected_records=(
            ExpectedRecord(
                state="present",
                table="invariant",
                record_id=invariant_id,
                digest=stale_digest,
            ),
        ),
        commands=(
            SetInvariantLabel(
                invariant_id=invariant_id,
                display_label="must not be written",
                expected_row_digest=stale_digest,
            ),
        ),
    )

    evidence = measure_refusal(candidate, batch)

    assert evidence.result.state == "refused"
    assert evidence.refusal.code == "stale_precondition"
    assert evidence.refusal.table == "invariant"
    assert evidence.refusal.record_id == invariant_id
    assert evidence.refusal.observed != stale_digest
    assert evidence.wrote_nothing()

    store = candidate.open()
    try:
        invariant = store.get_invariant(invariant_id)
        assert invariant is not None
        assert invariant.display_label == "seeded obligation 0"
    finally:
        store.close()


def test_an_expected_absence_that_is_not_absent_refuses_the_batch(
    candidate: CandidateHarness,
) -> None:
    """Expecting an identity to be absent when it is stored refuses before any command runs."""

    (invariant_id, _) = candidate.seed(1)[0]
    batch = ChangeBatch(
        expected=candidate.context(),
        expected_records=(
            ExpectedRecord(state="absent", table="invariant", record_id=invariant_id),
        ),
        commands=(AddInvariant(invariant_id=invariant_id, display_label="must not be written"),),
    )

    evidence = measure_refusal(candidate, batch)

    assert evidence.result.state == "refused"
    assert evidence.refusal.code == "duplicate_identity"
    assert evidence.wrote_nothing()
    assert evidence.counts_after["invariant"] == 1


def test_a_payload_that_claims_its_own_author_and_approval_is_not_an_authority(
    candidate: CandidateHarness,
) -> None:
    """The admitted author is stored, and no field of the payload can substitute for it.

    The draft below carries a provenance envelope naming a different actor and an authorization of
    its own. The operation re-stamps every stored row with the admitted envelope, so the spoofed
    values appear nowhere in the database -- which is what makes "a JSON author field is never a
    credential" a fact about the stored rows rather than a rule about the request.
    """

    seeds = CommandSeeds()
    spoofed = revision_draft(
        candidate, invariant_id=seeds.invariant_id, revision_id=seeds.revision_id
    ).model_copy(
        update={
            "provenance": candidate.authorship.model_copy(
                update={
                    "actor_ref": "agent:not-the-caller",
                    "authorization_ref": "approved by nobody",
                    "operation_id": uuid4(),
                    "recorded_at": "2000-01-01T00:00:00+00:00",
                }
            )
        }
    )
    result = candidate.apply(
        candidate.batch(
            AddInvariant(invariant_id=seeds.invariant_id, display_label="spoof invariant"),
            AddInvariantRevision(revision=spoofed),
        )
    )
    assert result.state == "changed"

    store = candidate.open()
    try:
        stored = store.get_revision(seeds.revision_id)
        assert stored is not None
        assert stored.revision.provenance.actor_ref == candidate.authorship.actor_ref
        assert stored.revision.provenance.authorization_ref == (
            candidate.authorship.authorization_ref
        )
        assert stored.revision.provenance.operation_id != spoofed.provenance.operation_id
        assert stored.revision.provenance.recorded_at != spoofed.provenance.recorded_at
    finally:
        store.close()


def test_an_accepted_origin_state_is_refused_as_a_promotion(
    candidate: CandidateHarness,
) -> None:
    """A command carrying accepted origin data is refused, because this operation promotes nothing.

    The refusal is checked before any DML, so the invariant the same batch also adds is not stored:
    a batch that tried to smuggle an acceptance in beside an ordinary record does not get the
    ordinary record either.
    """

    seeds = CommandSeeds()
    accepted = RevisionDraft(
        revision_id=seeds.revision_id,
        invariant_id=seeds.invariant_id,
        display_version="v1",
        statement="A statement that claims it was already accepted.",
        applicability="Every admitted candidate write in this namespace.",
        state_at_origin="accepted",
        acceptance_ref="approval:nonexistent",
        provenance=candidate.authorship,
    )
    evidence = measure_refusal(
        candidate,
        candidate.batch(
            AddInvariant(invariant_id=seeds.invariant_id, display_label="promotion invariant"),
            AddInvariantRevision(revision=accepted),
        ),
    )

    assert evidence.result.state == "refused"
    assert evidence.refusal.code == "promotion_not_supported"
    assert evidence.wrote_nothing()
    assert evidence.counts_after["invariant"] == 0


def test_a_baseline_lane_is_refused_as_a_non_candidate_target(
    candidate: CandidateHarness,
) -> None:
    """A batch addressed to the baseline lane is refused by name and writes nothing."""

    baseline = build_candidate_harness(
        Path(candidate.database_path.parent) / "baseline", lane="baseline"
    )
    evidence = measure_refusal(
        baseline,
        baseline.batch(
            AddInvariant(invariant_id=str(uuid4()), display_label="must not be written")
        ),
    )

    assert evidence.result.state == "refused"
    assert evidence.refusal.code == "target_not_candidate"
    assert evidence.refusal.observed == "baseline"
    assert evidence.wrote_nothing()


def test_a_batch_from_another_namespace_is_refused(candidate: CandidateHarness) -> None:
    """A context naming a different repository than the destination is refused.

    The check happens inside the transaction, against the namespace the store is actually bound to,
    so a batch cannot reach another repository's knowledge by carrying a context that claims to.
    """

    foreign = build_candidate_harness(Path(candidate.database_path.parent) / "foreign")
    mismatched = ChangeBatch(
        expected=foreign.context(),
        commands=(AddInvariant(invariant_id=str(uuid4()), display_label="cross namespace"),),
    )

    evidence = measure_refusal(candidate, mismatched)

    assert evidence.result.state == "refused"
    assert evidence.refusal.code == "stale_precondition"
    assert evidence.refusal.expected == candidate.repository_id
    assert evidence.refusal.observed == foreign.repository_id
    assert evidence.wrote_nothing()


def test_two_commands_writing_one_identity_in_a_batch_are_refused(
    candidate: CandidateHarness,
) -> None:
    """One batch may not author one identity twice, and the dataset is left untouched."""

    seeds = CommandSeeds()
    duplicate = candidate.batch(
        AddInvariant(invariant_id=seeds.invariant_id, display_label="first statement"),
        AddInvariant(invariant_id=seeds.invariant_id, display_label="second statement"),
    )

    evidence = measure_refusal(candidate, duplicate)

    assert evidence.result.state == "refused"
    assert evidence.refusal.code == "duplicate_identity"
    assert evidence.refusal.record_id == seeds.invariant_id
    assert evidence.wrote_nothing()
    assert evidence.counts_after["invariant"] == 0


def test_a_batch_may_cite_a_record_an_earlier_command_created(
    candidate: CandidateHarness,
) -> None:
    """A claim may cite the anchor and revision that earlier commands in the same batch created.

    This is the positive side of the ordering rule: a batch is one authored act, so its commands see
    each other -- while there is still no forward reference, because a command may only cite what
    came before it in the sequence it declared.
    """

    seeds = CommandSeeds()
    result = candidate.apply(
        candidate.batch(
            AddInvariant(invariant_id=seeds.invariant_id, display_label="ordered invariant"),
            AddInvariantRevision(
                revision=revision_draft(
                    candidate, invariant_id=seeds.invariant_id, revision_id=seeds.revision_id
                )
            ),
            claim_command(seeds, revision_id=seeds.revision_id, path="src/in_batch_reference.py"),
        )
    )

    assert result.state == "changed"
    assert result.refusal is None
    assert {row.table for row in result.changed} == {
        "invariant",
        "invariant_revision",
        "realization_claim",
        "source_anchor",
    }


def test_a_batch_may_author_its_lineage_in_any_order(candidate: CandidateHarness) -> None:
    """A successor may cite a predecessor a *later* command in the same batch creates.

    Validation is over the completed graph, so the order the author wrote the commands in is not the
    axis the declarations are checked on: the successor is judged against the graph the finished
    batch describes. Both revisions must be stored, and the successor's predecessor set must read
    back as the one that was declared.
    """

    seeds = CommandSeeds()
    result = candidate.apply(
        candidate.batch(
            AddInvariant(invariant_id=seeds.invariant_id, display_label="forward invariant"),
            AddInvariantRevision(
                revision=revision_draft(
                    candidate,
                    invariant_id=seeds.invariant_id,
                    revision_id=seeds.successor_id,
                    predecessors=(seeds.revision_id,),
                )
            ),
            AddInvariantRevision(
                revision=revision_draft(
                    candidate, invariant_id=seeds.invariant_id, revision_id=seeds.revision_id
                )
            ),
        )
    )

    assert result.state == "changed"
    assert result.refusal is None
    assert {(row.table, row.record_id, row.state) for row in result.changed} == {
        ("invariant", seeds.invariant_id, "written"),
        ("invariant_revision", seeds.revision_id, "written"),
        ("invariant_revision", seeds.successor_id, "written"),
    }

    store = candidate.open()
    try:
        successor = store.get_revision(seeds.successor_id)
        assert successor is not None
        assert successor.predecessors_sorted == (seeds.revision_id,)
        assert store.get_revision(seeds.revision_id) is not None
    finally:
        store.close()


def test_a_cycle_the_batch_declares_among_its_own_revisions_is_refused_by_name(
    candidate: CandidateHarness,
) -> None:
    """Two of the batch's own revisions naming each other are refused as a lineage cycle.

    Neither revision exists yet, so the database cannot catch this and no stored edge is involved:
    the cycle lives entirely in the batch's own declarations. It is refused before any row is
    written, with the revisions on the cycle named, which is what the completed-graph rule exists
    for.
    """

    seeds = CommandSeeds()
    evidence = measure_refusal(
        candidate,
        candidate.batch(
            AddInvariant(invariant_id=seeds.invariant_id, display_label="cyclic invariant"),
            AddInvariantRevision(
                revision=revision_draft(
                    candidate,
                    invariant_id=seeds.invariant_id,
                    revision_id=seeds.revision_id,
                    predecessors=(seeds.successor_id,),
                )
            ),
            AddInvariantRevision(
                revision=revision_draft(
                    candidate,
                    invariant_id=seeds.invariant_id,
                    revision_id=seeds.successor_id,
                    predecessors=(seeds.revision_id,),
                )
            ),
        ),
    )

    assert evidence.result.state == "refused"
    assert evidence.refusal.code == "lineage_cycle"
    assert evidence.refusal.table == "invariant_predecessor"
    members = {seeds.revision_id, seeds.successor_id}
    assert members <= set((evidence.refusal.observed or "").split(", "))
    assert evidence.wrote_nothing()
    assert evidence.counts_after["invariant"] == 0


def test_the_completed_graph_pass_refuses_a_cycle_the_operation_cannot_see_yet(
    candidate: CandidateHarness,
) -> None:
    """The declared graph is validated as a completed graph, before any row exists for it.

    The operation-level case above proves the refusal; this one drives the pass itself, because the
    property that makes it worth having is *when* it refuses: both revisions are absent from the
    database, so there is no stored edge and no row for SQLite to inspect. A pass that only looked
    at what is already stored would find nothing to refuse.
    """

    seeds = CommandSeeds()
    batch = candidate.batch(
        AddInvariant(invariant_id=seeds.invariant_id, display_label="cyclic invariant"),
        AddInvariantRevision(
            revision=revision_draft(
                candidate,
                invariant_id=seeds.invariant_id,
                revision_id=seeds.revision_id,
                predecessors=(seeds.successor_id,),
            )
        ),
        AddInvariantRevision(
            revision=revision_draft(
                candidate,
                invariant_id=seeds.invariant_id,
                revision_id=seeds.successor_id,
                predecessors=(seeds.revision_id,),
            )
        ),
    )

    store = candidate.open()
    try:
        assert store.get_revision(seeds.revision_id) is None
        assert store.get_revision(seeds.successor_id) is None
        with pytest.raises(KnowledgeRefused) as refused:
            require_preconditions(store, batch)
    finally:
        store.close()

    assert refused.value.refusal.code == "lineage_cycle"


def test_the_batch_cycle_rule_is_handed_the_batchs_own_declared_edges(
    candidate: CandidateHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The declared edges of the batch actually reach the rule that judges them.

    A batch-aware cycle check is only worth its name if the batch's own edges arrive at the shared
    rule: a check that gathered them and then judged the stored graph alone would refuse nothing and
    prove nothing. The spy below records what the rule was handed while a genuinely cyclic batch is
    refused, so the check dies both if the ruling call stops carrying the batch's edges and if it
    stops being reached at all.
    """

    seeds = CommandSeeds()
    batch = candidate.batch(
        AddInvariant(invariant_id=seeds.invariant_id, display_label="cyclic invariant"),
        AddInvariantRevision(
            revision=revision_draft(
                candidate,
                invariant_id=seeds.invariant_id,
                revision_id=seeds.revision_id,
                predecessors=(seeds.successor_id,),
            )
        ),
        AddInvariantRevision(
            revision=revision_draft(
                candidate,
                invariant_id=seeds.invariant_id,
                revision_id=seeds.successor_id,
                predecessors=(seeds.revision_id,),
            )
        ),
    )

    handed: list[tuple[tuple[str, str], ...]] = []
    original = lineage.find_cycle

    def spy(
        *,
        candidate_id: str,
        predecessors: Iterable[str],
        edges: Iterable[LineageEdge],
        extra_predecessors: Iterable[LineageEdge] = (),
    ) -> lineage.CycleFinding | None:
        handed.append(tuple(extra_predecessors))
        return original(
            candidate_id=candidate_id,
            predecessors=predecessors,
            edges=edges,
            extra_predecessors=extra_predecessors,
        )

    store = candidate.open()
    try:
        monkeypatch.setattr(lineage, "find_cycle", spy)
        with pytest.raises(KnowledgeRefused) as refused:
            require_preconditions(store, batch)
    finally:
        store.close()

    assert refused.value.refusal.code == "lineage_cycle"
    assert handed, "the batch-aware check never reached the shared lineage rule"
    assert any(extra for extra in handed), (
        "the shared rule was reached without the batch's declared edges: "
        f"every call carried {handed!r}"
    )
    assert {
        (seeds.revision_id, seeds.successor_id),
        (seeds.successor_id, seeds.revision_id),
    } & {edge for extra in handed for edge in extra}


def test_a_cycle_among_a_batchs_own_family_revisions_is_refused_by_name(
    candidate: CandidateHarness,
) -> None:
    """The family lineage applies the same completed-graph rule to the batch's own declarations."""

    seeds = CommandSeeds()
    evidence = measure_refusal(
        candidate,
        candidate.batch(
            AddFamily(family_id=seeds.family_id, display_label="cyclic family"),
            AddFamilyRevision(
                revision=family_revision_draft(
                    candidate,
                    family_id=seeds.family_id,
                    revision_id=seeds.family_revision_id,
                    predecessors=(seeds.member_id,),
                )
            ),
            AddFamilyRevision(
                revision=family_revision_draft(
                    candidate,
                    family_id=seeds.family_id,
                    revision_id=seeds.member_id,
                    predecessors=(seeds.family_revision_id,),
                )
            ),
        ),
    )

    assert evidence.result.state == "refused"
    assert evidence.refusal.code == "lineage_cycle"
    assert evidence.refusal.table == "family_predecessor"
    assert evidence.wrote_nothing()


def test_a_mixed_batch_reports_both_the_removal_and_the_write(
    candidate: CandidateHarness,
) -> None:
    """A batch that removes one record and adds another describes both in its receipt.

    A removal is a change, so it is an entry of its own -- ``state="removed"``, carrying the identity
    and the digest the deleted row had -- beside the row the batch wrote. A receipt that reported
    only the write would tell a caller the deleted row was still there.
    """

    seeds = CommandSeeds()
    assert (
        candidate.apply(
            candidate.batch(
                AddInvariant(invariant_id=seeds.invariant_id, display_label="removal invariant"),
                AddInvariantRevision(
                    revision=revision_draft(
                        candidate, invariant_id=seeds.invariant_id, revision_id=seeds.revision_id
                    )
                ),
                claim_command(seeds, revision_id=seeds.revision_id, path="src/removal_case.py"),
            )
        ).state
        == "changed"
    )

    store = candidate.open()
    try:
        claim = realizations.get_realization_claim(store, seeds.claim_id)
        assert claim is not None
        row_digest = claim.row_digest
    finally:
        store.close()

    removed = candidate.apply(
        candidate.batch(
            RemoveRealizationClaim(claim_id=seeds.claim_id, expected_row_digest=row_digest),
            AddInvariantRevision(
                revision=revision_draft(
                    candidate,
                    invariant_id=seeds.invariant_id,
                    revision_id=seeds.successor_id,
                    predecessors=(seeds.revision_id,),
                )
            ),
        )
    )

    assert removed.state == "changed"
    assert [(row.table, row.state) for row in removed.changed] == [
        ("realization_claim", "removed"),
        ("invariant_revision", "written"),
    ]
    removal = removed.changed[0]
    assert removal.record_id == seeds.claim_id
    assert removal.digest == row_digest
    assert removed.after.logical_digest == candidate.logical_digest()

    store = candidate.open()
    try:
        assert realizations.get_realization_claim(store, seeds.claim_id) is None
        assert anchors.get_anchor(store, str(seeds.anchor_id)) is not None
    finally:
        store.close()


def test_an_unknown_invariant_refuses_the_revision_and_writes_nothing(
    candidate: CandidateHarness,
) -> None:
    """A revision whose owning identity is not in the namespace refuses before any row is written."""

    seeds = CommandSeeds()
    evidence = measure_refusal(
        candidate,
        candidate.batch(
            AddInvariantRevision(
                revision=revision_draft(
                    candidate, invariant_id=seeds.invariant_id, revision_id=seeds.revision_id
                )
            )
        ),
    )

    assert evidence.result.state == "refused"
    assert evidence.refusal.code == "unknown_invariant"
    assert evidence.wrote_nothing()


def test_a_no_op_label_edit_inside_a_mixed_batch_is_not_reported_as_a_write(
    candidate: CandidateHarness,
) -> None:
    """A label edit that already holds reports nothing, while the batch's real write still reports.

    The receipt's contract is the rows the batch touched. A command that ran no statement touched
    nothing, so a consumer reading the receipt -- the comparison leaf, which uses it as the exact
    list of what changed -- must not be told that an unchanged row moved.
    """

    (invariant_id, _) = candidate.seed(1)[0]
    current_digest = invariant_digest(candidate, invariant_id)
    store = candidate.open()
    try:
        current = store.get_invariant(invariant_id)
        assert current is not None
        current_label = current.display_label
    finally:
        store.close()

    seeds = CommandSeeds()
    result = candidate.apply(
        candidate.batch(
            SetInvariantLabel(
                invariant_id=invariant_id,
                display_label=current_label,
                expected_row_digest=current_digest,
            ),
            AddInvariant(invariant_id=seeds.invariant_id, display_label="the real write"),
        )
    )

    assert result.state == "changed"
    assert [(row.table, row.record_id, row.state) for row in result.changed] == [
        ("invariant", seeds.invariant_id, "written")
    ]

    store = candidate.open()
    try:
        unchanged = store.get_invariant(invariant_id)
        assert unchanged is not None
        assert unchanged.row_digest == current_digest
        assert unchanged.display_label == current_label
    finally:
        store.close()


def test_a_batch_with_no_net_change_commits_nothing_and_reports_no_change(
    candidate: CandidateHarness,
) -> None:
    """A batch whose commands produce no logical change confirms rather than mutates.

    The label edit below names the label the invariant already displays, so under one rule for
    "nothing changed" the batch reports ``no_change`` and leaves the dataset identity, the stored
    rows and the logical digest exactly where they were. The failure this catches is a batch that
    recorded an audit timestamp or a counter row for having been submitted at all.
    """

    (invariant_id, _) = candidate.seed(1)[0]
    store = candidate.open()
    try:
        current = store.get_invariant(invariant_id)
        assert current is not None
        current_label = current.display_label
        current_digest = current.row_digest
    finally:
        store.close()

    counts_before = candidate.table_counts()
    digest_before = candidate.logical_digest()

    result = candidate.apply(
        candidate.batch(
            SetInvariantLabel(
                invariant_id=invariant_id,
                display_label=current_label,
                expected_row_digest=current_digest,
            )
        )
    )

    assert result.state == "no_change"
    assert result.changed == ()
    assert result.refusal is None
    assert result.before == result.after
    assert result.after.logical_digest == digest_before
    assert candidate.table_counts() == counts_before


def test_a_refused_batch_leaves_no_audit_row_of_its_own(candidate: CandidateHarness) -> None:
    """A refusal writes neither the requested change nor a record that it was refused.

    The temptation this catches is a "failed batch" audit row: a transient receipt that quietly makes
    the canonical tables a log of attempts rather than a description of the dataset.
    """

    seeds = CommandSeeds()
    evidence: RefusalEvidence = measure_refusal(
        candidate,
        candidate.batch(
            AddInvariantRevision(
                revision=revision_draft(
                    candidate, invariant_id=seeds.invariant_id, revision_id=seeds.revision_id
                )
            )
        ),
    )

    assert evidence.wrote_nothing()
    assert evidence.counts_before == evidence.counts_after
    assert sum(evidence.counts_after.values()) == 1  # the repository row, and nothing else


def _apply_one_removal(candidate: CandidateHarness, command: ChangeCommand) -> RefusalEvidence:
    """Apply one removal-only batch, measuring the dataset on both sides of it.

    The measurement is taken through separately opened stores, so what proves the deletion is the
    database's own state rather than the operation's receipt.
    """

    counts_before = candidate.table_counts()
    digest_before = candidate.logical_digest()
    result = candidate.apply(candidate.batch(command))
    counts_after = candidate.table_counts()
    digest_after = candidate.logical_digest()
    return RefusalEvidence(
        result=result,
        counts_before=counts_before,
        counts_after=counts_after,
        digest_before=digest_before,
        digest_after=digest_after,
    )


def test_a_removal_only_batch_of_each_kind_returns_a_typed_result(
    candidate: CandidateHarness,
) -> None:
    """A batch whose only effect is a deletion completes, for each of the three removal commands.

    A removal moves the dataset without writing a row, so the receipt has to be able to say so: the
    entry carries ``state="removed"``, the identity that is gone and the digest that row had. Three
    of the twelve commands would otherwise be unusable on their own, and the operation would return
    an exception where the requirement demands a structured result.
    """

    removals = removal_seeds(candidate)
    for removal in removals:
        evidence = _apply_one_removal(candidate, removal.command)
        assert evidence.result.state == "changed", removal.kind
        assert evidence.result.refusal is None, removal.kind
        assert evidence.result.before.logical_digest != evidence.result.after.logical_digest
        assert [row.state for row in evidence.result.changed] == ["removed"], removal.kind
        entry = evidence.result.changed[0]
        assert (entry.table, entry.record_id) == (removal.table, removal.record_id)
        assert entry.digest == removal.digest
        assert evidence.counts_before[removal.table] == evidence.counts_after[removal.table] + 1

        store = candidate.open()
        try:
            assert record_is_gone(candidate, removal.table, removal.record_id), removal.kind
        finally:
            store.close()


def test_a_removal_only_batch_removing_all_three_kinds_at_once_returns_a_typed_result(
    candidate: CandidateHarness,
) -> None:
    """One batch removing an anchor, a membership and a claim reports all three removals.

    The three removals in one batch are the case that has no written row at all, so the receipt is
    made entirely of removal entries and the dataset identity still has to move.
    """

    removals = removal_seeds(candidate)
    commands = tuple(removal.command for removal in removals)
    context = candidate.context()
    result = candidate.apply(ChangeBatch(expected=context, commands=commands))

    assert result.state == "changed"
    assert result.refusal is None
    assert result.before.logical_digest != result.after.logical_digest
    assert {(row.table, row.state) for row in result.changed} == {
        (removal.table, "removed") for removal in removals
    }
    assert {row.record_id for row in result.changed} == {removal.record_id for removal in removals}

    store = candidate.open()
    try:
        for removal in removals:
            assert record_is_gone(candidate, removal.table, removal.record_id), removal.kind
    finally:
        store.close()


def test_a_database_refusal_mid_batch_names_the_command_that_actually_failed(
    candidate: CandidateHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A constraint the database enforces refuses the batch naming the command that hit it.

    The concept's own pair pre-check runs first and would refuse this duplicate in its own
    vocabulary, so the pre-check is patched away inside the case: what is left is the declared
    unique tuple, which only SQLite can refuse. The mapped refusal must then name command 2 -- the
    membership that actually collided -- and not command 3, the last command the caller declared. A
    caller sent to the wrong command cannot repair anything.
    """

    (_, invariant_revision_id) = candidate.seed(1)[0]
    family = CommandSeeds()
    assert (
        candidate.apply(
            candidate.batch(
                AddFamily(family_id=family.family_id, display_label="raw pair family"),
                AddFamilyRevision(
                    revision=family_revision_draft(
                        candidate,
                        family_id=family.family_id,
                        revision_id=family.family_revision_id,
                    )
                ),
            )
        ).state
        == "changed"
    )
    # The pair the batch later re-states is already related by a row written outside the operation.
    insert_raw_membership(
        candidate,
        family_revision_id=family.family_revision_id,
        invariant_revision_id=invariant_revision_id,
        member_id=uuid4(),
    )
    # The concept's pair pre-check would refuse this duplicate in its own vocabulary, so it is taken
    # out of the way -- inside its own duration, so that what the batch meets is the declared unique
    # tuple and nothing else. The guard is restored before the batch runs, because a node that left
    # it patched would be measuring its own test double rather than the operation.
    seeds = CommandSeeds()
    guarded = candidate.batch(
        AddInvariant(invariant_id=seeds.invariant_id, display_label="before the failure"),
        AddInvariant(invariant_id=seeds.successor_id, display_label="also before it"),
        AddFamilyMember(
            member=FamilyMemberDraft(
                member_id=str(uuid4()),
                family_revision_id=family.family_revision_id,
                invariant_revision_id=invariant_revision_id,
                provenance=candidate.authorship,
            )
        ),
        AddInvariant(invariant_id=seeds.member_id, display_label="never reached"),
    )
    # The guard is taken out of the way for the length of this batch and restored immediately after,
    # so the node measures the database path and leaves the concept exactly as it found it. A node
    # that left it patched would be measuring its own test double rather than the operation.
    with monkeypatch.context() as patched:
        patched.setattr(memberships, "find_membership_by_pair", lambda *_: None)
        evidence = measure_refusal(candidate, guarded)

    assert evidence.result.state == "refused"
    assert evidence.refusal.code == "relationship_constraint"
    assert evidence.refusal.operation == "change_candidate"
    assert evidence.refusal.record_id == "2:add_family_member"
    assert evidence.wrote_nothing()
    assert evidence.counts_after["invariant"] == 1


def test_the_membership_guard_refuses_a_pair_the_declared_unique_tuple_would_also_refuse(
    candidate: CandidateHarness,
) -> None:
    """The membership operation's own pair guard refuses a duplicate before the database has to.

    The guard is the layer that gives the caller a typed refusal naming both the attempted relation
    and the stored one that already relates the pair; the declared unique tuple is behind it. The
    row is written outside the operation precisely so the pair is already related, and the refusal
    is read through the in-transaction entry point, which is what the operation calls.
    """

    (_, invariant_revision_id) = candidate.seed(1)[0]
    family = CommandSeeds()
    assert (
        candidate.apply(
            candidate.batch(
                AddFamily(family_id=family.family_id, display_label="guarded family"),
                AddFamilyRevision(
                    revision=family_revision_draft(
                        candidate,
                        family_id=family.family_id,
                        revision_id=family.family_revision_id,
                    )
                ),
            )
        ).state
        == "changed"
    )
    stored_member_id = uuid4()
    insert_raw_membership(
        candidate,
        family_revision_id=family.family_revision_id,
        invariant_revision_id=invariant_revision_id,
        member_id=stored_member_id,
    )

    store = candidate.open()
    try:
        with pytest.raises(KnowledgeRefused) as refused:
            memberships.insert_family_member(
                store,
                FamilyMember(
                    repository_id=candidate.repository_id,
                    member_id=str(uuid4()),
                    family_revision_id=family.family_revision_id,
                    invariant_revision_id=invariant_revision_id,
                    provenance=candidate.authorship,
                    row_digest="0" * 64,
                ),
            )
    finally:
        store.close()

    assert refused.value.refusal.code == "relationship_constraint"
    assert refused.value.refusal.table == "family_member"
    assert refused.value.refusal.observed == str(stored_member_id)


def test_a_context_smuggled_past_the_model_seal_is_refused_by_the_operation(
    candidate: CandidateHarness,
) -> None:
    """The operation re-derives the context digest, so a validator-bypassing context is refused.

    ``model_copy`` builds a batch without re-running the context's own seal check, which is exactly
    how a context assembled field by field could reach the store. What refuses it is the comparison
    the operation performs inside its own transaction, and the refusal names both digests with the
    dataset left untouched.
    """

    (invariant_id, _) = candidate.seed(1)[0]
    honest = candidate.batch(
        SetInvariantLabel(
            invariant_id=invariant_id,
            display_label="smuggled context label",
            expected_row_digest=invariant_digest(candidate, invariant_id),
        )
    )
    tampered_context = honest.expected.model_copy(update={"candidate_ref": "draft:elsewhere"})
    smuggled = honest.model_copy(update={"expected": tampered_context})

    counts_before = candidate.table_counts()
    result = candidate.apply(smuggled)

    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "stale_precondition"
    assert result.refusal.expected == tampered_context.context_digest
    assert result.refusal.observed != tampered_context.context_digest
    assert result.before == result.after
    assert candidate.table_counts() == counts_before

    store = candidate.open()
    try:
        invariant = store.get_invariant(invariant_id)
        assert invariant is not None
        assert invariant.display_label == "seeded obligation 0"
    finally:
        store.close()


def test_a_task_candidate_lane_is_refused_until_its_binding_can_be_resolved(
    candidate: CandidateHarness,
) -> None:
    """A task-candidate context with no resolved binding is refused, and nothing is written.

    The packet requires a task-bound candidate to use its existing contract authority, which this
    operation cannot resolve. Accepting the lane on a caller-supplied reference would fail open on
    exactly that sentence, so the lane fails closed until a resolved binding is available to check.
    """

    task_lane = build_candidate_harness(
        Path(candidate.database_path.parent) / "task-lane", lane="task-candidate"
    )
    evidence = measure_refusal(
        task_lane,
        task_lane.batch(
            AddInvariant(invariant_id=str(uuid4()), display_label="must not be written")
        ),
    )

    assert evidence.result.state == "refused"
    assert evidence.refusal.code == "unauthorized_scope"
    assert evidence.refusal.operation == "change_candidate"
    assert evidence.refusal.expected == "a resolved, owner-validated task binding"
    assert evidence.refusal.observed == "<no task reference supplied>"
    assert evidence.wrote_nothing()
    assert evidence.counts_after["invariant"] == 0
