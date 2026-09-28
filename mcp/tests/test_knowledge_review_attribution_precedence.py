"""Attribution precedence across knowledge availability, over one real review enclosure.

These cases build the same real enclosure, linked Git worktree and capture as
:mod:`test_knowledge_review_source_endpoints`, through that module's endpoint fixture, and open the
review through the production resolution and composition. What varies is only the state of the
knowledge halves beside the one bound code-tree pair: both readable, one gone, one replaced by an
explicitly identified empty first generation, one whose origin record disagrees with its bytes, and a
candidate receipt that cannot be read. The source inventory is measured from the pair in every case,
so the only thing that moves is which attribution conclusions the review may state -- a half nobody
read never turns "not looked for" into "nobody registered it", and a damaged half never supports a
negative conclusion. Nothing here injects a partition, a mapping or a payload.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from agents_remember.application import review_task_context
from agents_remember.application.knowledge_before_half import (
    BaselineOrigin,
    baseline_database_path,
    read_before_half,
    write_baseline_origin,
)
from agents_remember.application.knowledge_review import read_knowledge_review
from agents_remember.application.knowledge_snapshot import (
    admitted_candidate_destination,
    create_knowledge_candidate,
)
from agents_remember.application.review_candidate_resolution import ReviewCandidateResolution
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.models.knowledge.candidate import CandidateResolution
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.review import ReviewRefusal
from agents_remember.models.knowledge.snapshot import CANDIDATE_RECEIPT_NAME
from diff_scope_test_support import SUCCESSOR_PATH, UNMAPPED_PATH
from test_knowledge_review_source_endpoints import LEAF_ID, EndpointFixture, build_endpoint_fixture

pytestmark = pytest.mark.evidence_unit


# --- the attribution precedence across knowledge availability (ICR-R04) --------------------------
#
# The three cases below are one comparison of three knowledge states of the *same* real enclosure, the
# same capture and the same bound code-tree pair: both halves readable, one half gone, and one half
# replaced by an explicitly identified empty first generation. The source inventory is measured from
# the pair in all three -- what moves is only which conclusions the attribution can reach, which is
# exactly the precedence the packet fixes. Nothing here injects a partition, a mapping or a payload.


def test_one_unreadable_knowledge_half_leaves_the_readable_side_attributed_and_the_rest_unknown(
    tmp_path: Path,
) -> None:
    """A half nobody read cannot turn "not looked for" into "nobody registered it".

    The baseline dataset is removed after the pair resolves, which is the state a curator meets when
    a task's forked-from dataset is lost: the candidate half is there and readable, the baseline is
    not. Two conclusions have to survive together. A path the *readable* candidate registers stays
    attributed -- a positive mapping does not need the other side's agreement -- while every path it
    does not register is of *undetermined* attribution, never confirmed unregistered, because the
    baseline was not inspected and an unread snapshot is not one that registered nothing. The
    confirmed-unregistered list is therefore empty and the count of unattributed changes is a measured
    zero of a defined bucket, with the undetermined paths carried by the partition beside it.
    """

    fixture = build_endpoint_fixture(tmp_path / "one-half-gone")
    resolved = fixture.resolve()
    baseline = resolved.baseline_database
    assert baseline.is_file()
    baseline.unlink()

    result = read_knowledge_review(fixture.config, fixture.task_request())

    assert result.state == "review", result.refusal
    payload = result.payload
    assert payload is not None
    assert payload.source.inventory.state == "measured"
    partition = payload.source.attribution
    assert partition is not None and partition.state == "measured"
    assert partition.complete is False
    assert [entry.state for entry in partition.sides] == ["unavailable", "inspected"]
    by_path = {entry.path: entry for entry in partition.paths}
    # The readable side's own mapping stands, and the path it does not mention is not a conclusion.
    assert by_path[SUCCESSOR_PATH].bucket == "attributed"
    assert by_path[SUCCESSOR_PATH].mapped_sides == ("after",)
    # ... and the attribution it *does* establish is labelled as possibly incomplete, which is the
    # sentence the packet asks for: the mapping stands and its additional mappings may be unknown.
    assert "additional mappings may be unknown" in by_path[SUCCESSOR_PATH].detail
    assert by_path[UNMAPPED_PATH].bucket == "unknown_attribution"
    assert by_path[UNMAPPED_PATH].link is None
    assert payload.source.attributed_changed_paths == partition.attributed_paths
    assert payload.source.unattributed_changed_paths == ()
    assert UNMAPPED_PATH in payload.source.unknown_attribution_changed_paths
    counts = {entry.name: entry.value for entry in payload.source.remaining}
    assert counts["unattributed_changed_paths"] == 0
    # THE ZERO IS SCOPED AND THE LIMIT IS DECLARED AT THE TOP LEVEL (ICR-R04). This route makes no
    # comparison, so it has no result vocabulary to inherit the declaration from: it states the same
    # two facts from the partition itself, and the count of undetermined changes travels beside the
    # zero so a reader of `limitations` + `remaining` alone -- which is the surface the dashboard
    # renders -- cannot read "0 unattributed" as completeness.
    assert counts["unknown_attribution_changed_paths"] == partition.unknown_attribution_total
    assert partition.unknown_attribution_total
    assert "limitation:unknown_attribution_changed_paths" in payload.limitations
    assert (
        f"omitted:attribution_not_determined:{partition.unknown_attribution_total}"
        in payload.limitations
    )
    undetermined_row = next(
        entry
        for entry in payload.source.remaining
        if entry.name == "unknown_attribution_changed_paths"
    )
    assert undetermined_row.value is not None and undetermined_row.reason is None
    # The absent half is stated where a reader looks for it -- in the knowledge pane's own detail and
    # in the partition's own side entry -- rather than being silently folded into the candidate's
    # silence. An *absent* half is deliberately not the "present but unreadable" state, so this route
    # names the absence it found instead of borrowing the other limit's word.
    assert "the resolved baseline dataset is absent" in (payload.knowledge.selection_detail or "")
    assert partition.sides[0].state == "unavailable"
    assert partition.sides[0].registered_mapping_count is None
    assert partition.sides[0].detail


def test_an_identified_empty_first_generation_counts_as_completely_inspected_for_absence(
    tmp_path: Path,
) -> None:
    """A recorded empty first generation supports the negative conclusion a missing half cannot.

    This is the other side of the precedence rule and the reason ``ICR-R05`` is a prerequisite: the
    before half here holds a schema-valid empty dataset *and* the origin record R05's own writer
    records for it, which is what makes its emptiness identified rather than unknown. With every
    required side complete, a changed path no valid mapping resolves is confirmed unregistered -- the
    conclusion a reader needs and the one an absent half must never be allowed to produce.
    """

    fixture = build_endpoint_fixture(tmp_path / "first-generation")
    resolved = fixture.resolve()
    half = resolved.baseline_database.parent
    shutil.rmtree(half)
    _identified_empty_first_generation(half, fixture)

    result = read_knowledge_review(fixture.config, fixture.task_request())

    assert result.state == "review", result.refusal
    payload = result.payload
    assert payload is not None
    partition = payload.source.attribution
    assert partition is not None and partition.state == "measured"
    assert partition.complete is True
    assert [entry.state for entry in partition.sides] == ["known_empty", "inspected"]
    by_path = {entry.path: entry for entry in partition.paths}
    assert by_path[SUCCESSOR_PATH].bucket == "attributed"
    assert by_path[UNMAPPED_PATH].bucket == "confirmed_unregistered"
    assert UNMAPPED_PATH in payload.source.unattributed_changed_paths
    assert payload.source.unknown_attribution_changed_paths == ()
    counts = {entry.name: entry.value for entry in payload.source.remaining}
    assert counts["unattributed_changed_paths"] == len(payload.source.unattributed_changed_paths)
    assert partition.unknown_attribution_total == 0


def test_the_same_pair_read_completely_confirms_absence_where_the_lost_half_could_not(
    tmp_path: Path,
) -> None:
    """The control for the two cases above: with both halves readable the same path is confirmed.

    Identical enclosure, identical capture, identical code-tree pair; the only difference is that the
    baseline dataset is the one the resolution placed. The path no valid mapping reaches is then
    *confirmed* unregistered rather than undetermined, so the two earlier results are attributable to
    the knowledge state and not to the fixture.
    """

    fixture = build_endpoint_fixture(tmp_path / "both-halves")

    result = read_knowledge_review(fixture.config, fixture.task_request())

    assert result.state == "review", result.refusal
    payload = result.payload
    assert payload is not None
    partition = payload.source.attribution
    assert partition is not None
    assert partition.complete is True
    assert [entry.state for entry in partition.sides] == ["inspected", "inspected"]
    by_path = {entry.path: entry for entry in partition.paths}
    assert by_path[UNMAPPED_PATH].bucket == "confirmed_unregistered"
    assert payload.source.unknown_attribution_changed_paths == ()
    assert partition.unknown_attribution_total == 0
    assert partition.changed_total == len(payload.source.inventory.entries)
    buckets = (
        partition.attributed_total,
        partition.confirmed_unregistered_total,
        partition.unknown_attribution_total,
    )
    assert None not in buckets
    assert partition.changed_total == sum(bucket for bucket in buckets if bucket is not None)


def test_a_damaged_knowledge_half_cannot_support_a_negative_attribution_conclusion(
    tmp_path: Path,
) -> None:
    """A half the response itself calls unreadable is not a half that registered nothing.

    The before half here is present, its bytes are a valid dataset, and the origin record beside it
    disagrees with those bytes -- R05's ``damaged`` state, and the exact state the review's own
    preflight refuses as "present but cannot be read". One payload must not say both things: if the
    response declares the half unreadable, the attribution partition cannot have inspected it, so every
    changed path it does not map is of *undetermined* attribution and the confirmed-unregistered list
    stays empty. The subject route keeps its refusal for the same pair, so the damage is named on both
    routes and read as a negative-conclusion bar on the one that serves a pane.
    """

    fixture = build_endpoint_fixture(tmp_path / "damaged-attribution")
    resolved = fixture.resolve()
    half = resolved.baseline_database.parent
    _damaged_origin(half, fixture)
    assert read_before_half(half).state == "damaged"

    result = read_knowledge_review(fixture.config, fixture.task_request())

    assert result.state == "review", result.refusal
    payload = result.payload
    assert payload is not None
    assert "limitation:knowledge_half_unreadable" in payload.limitations
    assert "present but cannot be read" in (payload.knowledge.selection_detail or "")
    partition = payload.source.attribution
    assert partition is not None and partition.state == "measured"
    assert partition.complete is False
    assert [entry.state for entry in partition.sides] == ["unavailable", "inspected"]
    assert partition.sides[0].registered_mapping_count is None
    # The negative conclusion is refused, and the paths it would have covered are undetermined.
    assert partition.confirmed_unregistered_total == 0
    assert payload.source.unattributed_changed_paths == ()
    assert partition.unknown_attribution_total
    assert set(payload.source.unknown_attribution_changed_paths) == {
        entry.path for entry in partition.paths if entry.bucket == "unknown_attribution"
    }
    counts = {entry.name: entry.value for entry in payload.source.remaining}
    assert counts["unattributed_changed_paths"] == 0
    assert counts["unknown_attribution_changed_paths"] == partition.unknown_attribution_total
    assert "limitation:unknown_attribution_changed_paths" in payload.limitations

    # The same pair on the subject route is the named refusal this state already earns.
    refused = read_knowledge_review(fixture.config, fixture.request())
    assert refused.state == "refused"
    assert refused.refusal is not None
    assert refused.refusal.code == "candidate_dataset_absent"


def test_an_unreadable_candidate_receipt_is_stated_on_the_task_route_and_refused_on_the_subject_route(
    tmp_path: Path,
) -> None:
    """A receipt nothing can read leaves no namespace, and no route may raise over it.

    The candidate's own receipt is the one authority for the namespace its dataset is bound to, so a
    receipt that exists and does not validate is a fact about the *pair*: neither half can be opened.
    The task-context route must state that -- it exists so a knowledge-half problem cannot remove the
    source review -- while the subject route keeps the typed refusal the shipped preflight already
    answers this state with. Both routes are asserted here on the same bytes, because the regression
    this pins was exactly a divergence between them: an escaping storage error on the one route.
    """

    fixture = build_endpoint_fixture(tmp_path / "unreadable-receipt")
    resolved = fixture.resolve()
    receipt = resolved.candidate_database.parent / CANDIDATE_RECEIPT_NAME
    receipt.write_text('{"not": "a candidate receipt"}', encoding="utf-8")

    result = read_knowledge_review(fixture.config, fixture.task_request())

    assert result.state == "review", result.refusal
    payload = result.payload
    assert payload is not None
    # The source review survives: the inventory is still the complete measurement of the bound pair.
    assert payload.source.inventory.state == "measured"
    assert payload.source.inventory.listed_total > 0
    # ... and the knowledge half is stated, at the top level and in the pane's own words.
    assert "limitation:knowledge_half_unreadable" in payload.limitations
    assert "is not a valid sealed receipt" in (payload.knowledge.selection_detail or "")
    # The actionable instruction the refusal carries reaches the served body too, not only the subject
    # route's refusal (F-V3-3): a reader of the task route is told what to repair.
    assert "repair the candidate's receipt and dataset" in (
        payload.knowledge.selection_detail or ""
    )
    partition = payload.source.attribution
    assert partition is not None and partition.state == "measured"
    assert partition.complete is False
    # ONLY THE SIDE THAT CANNOT BE BOUND IS UNAVAILABLE (F-V3-1, and ICR-R04's own precedence
    # sentence): the candidate half is read through the admission record that is broken, while the
    # before half's own bytes disclose the namespace it is bound to and its mappings resolve. Its paths
    # stay attributed, and every path only the unreadable side could have mapped is undetermined.
    assert [entry.state for entry in partition.sides] == ["inspected", "unavailable"]
    assert "is not a valid sealed receipt" in partition.sides[1].detail
    assert partition.attributed_total
    assert set(payload.source.attributed_changed_paths) == {
        entry.path for entry in partition.paths if entry.bucket == "attributed"
    }
    assert all(
        entry.mapped_sides == ("before",)
        for entry in partition.paths
        if entry.bucket == "attributed"
    )
    # No negative conclusion is drawn from a pair whose other side could not be opened, and every
    # count is scoped.
    assert partition.confirmed_unregistered_total == 0
    assert payload.source.unattributed_changed_paths == ()
    assert partition.unknown_attribution_total == len(
        payload.source.unknown_attribution_changed_paths
    )
    assert partition.unknown_attribution_total
    assert "limitation:unknown_attribution_changed_paths" in payload.limitations
    assert (
        f"omitted:attribution_not_determined:{partition.unknown_attribution_total}"
        in payload.limitations
    )
    counts = {entry.name: entry.value for entry in payload.source.remaining}
    assert counts["unattributed_changed_paths"] == 0
    assert counts["unknown_attribution_changed_paths"] == partition.unknown_attribution_total

    # The subject route's own behaviour is unchanged: the same bytes are the shipped typed refusal.
    refused = read_knowledge_review(fixture.config, fixture.request())

    assert refused.state == "refused"
    assert refused.refusal is not None
    assert refused.refusal.code == "candidate_dataset_absent"
    assert "could not be opened for review" in refused.refusal.detail
    assert "repair the candidate" in refused.refusal.next_action


def test_a_receipt_that_breaks_after_the_preflight_is_stated_in_the_pane_and_the_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The state a reader sees must not depend on *when* the record broke.

    The guard around the pair read exists because the receipt can move between the preflight and the
    sides being bound. When it does, the partition's side entry carries the reason -- and the pane and
    the declared limitations have to carry it too, or a consumer that renders only those reads a
    knowledge half that looks fine while the whole attribution is undetermined. The race cannot be
    produced by the filesystem inside one call, so the record is broken by the second question, at the
    call index the composition itself uses: preflight, then pair binding.
    """

    fixture = build_endpoint_fixture(tmp_path / "receipt-race")
    resolved = fixture.resolve()
    receipt = resolved.candidate_database.parent / CANDIDATE_RECEIPT_NAME
    genuine = review_task_context.candidate_receipt_refusal
    asked: list[int] = []

    def racing(candidate: ReviewCandidateResolution) -> ReviewRefusal | None:
        asked.append(len(asked) + 1)
        if len(asked) == 1:
            return None  # readable where the route preflights it
        receipt.write_text('{"not": "a candidate receipt"}', encoding="utf-8")
        return genuine(candidate)

    monkeypatch.setattr(review_task_context, "candidate_receipt_refusal", racing)

    result = read_knowledge_review(fixture.config, fixture.task_request())

    assert result.state == "review", result.refusal
    payload = result.payload
    assert payload is not None
    assert len(asked) >= 3, "the composition asks again before the payload is built"
    assert "limitation:knowledge_half_unreadable" in payload.limitations
    assert "is not a valid sealed receipt" in (payload.knowledge.selection_detail or "")
    assert "repair the candidate's receipt and dataset" in (
        payload.knowledge.selection_detail or ""
    )
    partition = payload.source.attribution
    assert partition is not None
    assert [entry.state for entry in partition.sides] == ["inspected", "unavailable"]
    assert "is not a valid sealed receipt" in partition.sides[1].detail
    assert partition.confirmed_unregistered_total == 0
    assert "limitation:unknown_attribution_changed_paths" in payload.limitations
    assert (
        f"omitted:attribution_not_determined:{partition.unknown_attribution_total}"
        in payload.limitations
    )


def _damaged_origin(half: Path, fixture: EndpointFixture) -> None:
    """Record an origin whose logical digest is not the dataset's, so the half reads damaged.

    The record is written by R05's own writer and the digest is the one thing it gets wrong, which is
    exactly the state a repeated successful ingest can leave behind in production.
    """

    identity = dataset_identity(baseline_database_path(half))
    write_baseline_origin(
        half,
        BaselineOrigin(
            repository_id=identity.repository_id,
            authority_home=fixture.repository_id,
            schema_version=identity.schema_version,
            logical_digest="1" * 64,
            code_base_commit=fixture.contract.code_base_commit,
            recorded_at="2026-09-21T00:00:00+00:00",
            leaf_id=LEAF_ID,
            contract_path=str(fixture.contract.contract_path),
            authorization_ref="ICR-R04 damaged-half case",
        ),
    )
    assert identity.logical_digest != "1" * 64


def _identified_empty_first_generation(half: Path, fixture: EndpointFixture) -> None:
    """Materialize R05's identified empty before half with R05's own creation owner and writer.

    The dataset is built by the shipped candidate-creation owner under the candidate's own recorded
    namespace, and the origin record is written by the shipped origin writer with the identity read
    back from the bytes that were created -- never with an identity taken from this case. This is the
    state ``establish_first_generation`` produces; building it from the two owners it composes keeps
    this case independent of the ingest CLI's own admission journey.
    """

    resolved = fixture.resolve()
    code_tree = resolved.candidate_code_tree_id or resolved.baseline_code_tree_id
    assert code_tree is not None
    created = create_knowledge_candidate(
        admitted_candidate_destination(
            half,
            RepositoryIdentity(
                repository_id=resolved.repository_id, authority_home=fixture.repository_id
            ),
            CandidateResolution(
                lane="task-candidate",
                code_tree_id=code_tree,
                memory_tree_id=code_tree,
                snapshot_ref="icr-r04-first-generation",
                candidate_ref="icr-r04-first-generation",
                task_ref=LEAF_ID,
            ),
        )
    )
    assert created.state == "created", created.refusal
    identity = dataset_identity(baseline_database_path(half))
    write_baseline_origin(
        half,
        BaselineOrigin(
            repository_id=identity.repository_id,
            authority_home=fixture.repository_id,
            schema_version=identity.schema_version,
            logical_digest=identity.logical_digest,
            code_base_commit=fixture.contract.code_base_commit,
            recorded_at="2026-09-21T00:00:00+00:00",
            leaf_id=LEAF_ID,
            contract_path=str(fixture.contract.contract_path),
            authorization_ref="ICR-R04 production composition case",
        ),
    )
    assert read_before_half(half).state == "identified"
