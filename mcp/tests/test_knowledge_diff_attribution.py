"""The comparison's source attribution partition: every measured changed path in exactly one bucket.

These cases drive the same real comparison as :mod:`test_knowledge_diff_scope`, over the two real
databases and Git trees :mod:`diff_scope_test_support` builds, and read the partition the
comparison's expansion publishes -- or the partition owner itself, where the case measures a
knowledge-availability state no fixture snapshot can reach. They share the sibling module's
comparison helpers rather than restating them, and occupy the same unit-regression lane for the same
reason: what they measure is the comparison's own accounting, not a process or a publication.

The properties, one case each: the measured change set is partitioned once into disjoint,
exhaustive buckets; a path realized only by another subject is attributed outside the selection
rather than read as unregistered; a family subject counts its own members' links; a registered
mapping that never resolved, or a stale one, establishes no attribution; one uninspected snapshot
makes every unmapped change undetermined; a legitimately empty side counts as completely inspected;
and an unavailable or partial partition never states a total it did not measure.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from agents_remember.application.review_source_inventory import review_inventory, source_tree_side
from agents_remember.memory.knowledge.diff_display import (
    MappingFact,
    SideInspection,
    TreeChange,
    TreePaths,
    partition_attribution,
    unavailable_attribution,
)
from agents_remember.memory.knowledge.read_anchors import observe_anchor
from agents_remember.memory.knowledge.read_queries import fetch_realizations_at_path
from agents_remember.models.knowledge.diff import SourceAttribution
from agents_remember.models.knowledge.read import FamilyIdentitySeed
from agents_remember.models.knowledge.source import FileLocator, SymbolLocator
from anchor_fixture_models import GitBlobIdentity, RealizationClaimDraft, SourceAnchorDraft
from diff_scope_test_support import DiffFixture, build_diff_fixture
from knowledge_rows_test_support import (
    NewAnchor,
    RealizationClaimRequest,
    open_knowledge_store,
    realizations,
)
from pydantic import ValidationError
from test_knowledge_diff_scope import diff_seed, run_diff

pytestmark = pytest.mark.evidence_unit


@pytest.fixture
def fixture(tmp_path: Path) -> DiffFixture:
    """One fresh two-snapshot fixture per case: no case can observe another's candidate state."""

    return build_diff_fixture(tmp_path / "diff")


# --- the attribution partition (ICR-R04) ------------------------------------------------------


def test_the_measured_changes_are_partitioned_once_and_the_buckets_are_disjoint_and_exhaustive(
    fixture: DiffFixture,
) -> None:
    """Every measured changed path is in exactly one attribution bucket, and only changed paths are.

    This is the accounting ``ICR-R04`` requires, measured against the two real trees: the source
    measurement's own change set is the denominator, the partition's three lists are disjoint and
    their union *is* that set, and the two facts that used to be wrong are asserted directly. A
    *mapped but unchanged* path (the sibling's ``src/anchors.py``) is in no list at all, so it can no
    longer increment a changed-path count; and a path two claims name (``src/batch.py``) is counted
    once, so a duplicate link cannot inflate a total. The three lists are read from the expansion and
    the denominator from the shipped inventory, so the two are independent renderings rather than one
    recomputed from the other.
    """

    result = run_diff(fixture, diff_seed(fixture))

    assert result.state == "page", result.refusal
    expansion = result.expansion
    assert expansion is not None
    partition = expansion.attribution
    assert partition is not None and partition.state == "measured"
    assert partition.granularity == "changed_path"
    assert partition.complete is True

    inventory = review_inventory(
        source_tree_side(fixture.before_tree_id, fixture.before.git_root),
        source_tree_side(fixture.after_tree_id, fixture.after.git_root),
    )
    measured = {entry.path for entry in inventory.entries}
    attributed = set(expansion.attributed_changed_paths)
    unregistered = set(expansion.unattributed_changed_paths)
    undetermined = set(expansion.unknown_attribution_changed_paths)
    assert not attributed & unregistered
    assert not attributed & undetermined
    assert not unregistered & undetermined
    assert attributed | unregistered | undetermined == measured
    assert partition.changed_total == len(measured) == len(partition.paths)
    assert (
        partition.attributed_total,
        partition.confirmed_unregistered_total,
        partition.unknown_attribution_total,
    ) == (len(attributed), len(unregistered), len(undetermined))

    # The defect this requirement exists for: an unchanged mapped file is context, not a change.
    assert fixture.unchanged_source_path not in attributed
    assert fixture.unchanged_source_path not in unregistered
    assert fixture.unchanged_source_path not in undetermined
    # Two claims on each side name the rewritten path and it is one changed path. Its mapping is
    # established by the *baseline's* claims, whose recorded bytes the baseline tree really holds; the
    # candidate's two claims recorded the same baseline bytes and are therefore STALE against the
    # candidate tree, so they are counted as unresolved purported mappings and the mapped side is the
    # one that resolved exactly (ICR-R04's "a stale or unresolved purported mapping alone does not
    # establish a valid mapping").
    by_path = {entry.path: entry for entry in partition.paths}
    rewritten = by_path[fixture.changed_source_path]
    assert rewritten.bucket == "attributed"
    assert rewritten.mapped_sides == ("before",)
    assert rewritten.unresolved_mapping_count == 2
    assert sum(1 for entry in partition.paths if entry.path == fixture.changed_source_path) == 1
    # The unmapped addition is the confirmed gap, and it carries no link.
    assert by_path[fixture.unattributed_path].bucket == "confirmed_unregistered"
    assert by_path[fixture.unattributed_path].link is None
    assert by_path[fixture.unattributed_path].unresolved_mapping_count == 0
    # The packet's own arithmetic: one changed unmapped file beside one unchanged mapped file is
    # *one* changed unregistered path, and the unchanged mapped file is in no list at all.
    assert unregistered == {fixture.unattributed_path}
    assert attributed == measured - unregistered
    # The supported granularity is stated rather than implied: a resolved path-level mapping is not a
    # claim that every changed line inside the path realizes the recorded claim.
    assert "never that every change inside it realizes that claim" in partition.detail
    assert entry_link(partition, fixture.unattributed_path) is None


def entry_link(partition: SourceAttribution, path: str) -> str | None:
    """The link label one measured path carries, or ``None`` when it is not attributed."""

    return next(entry.link for entry in partition.paths if entry.path == path)


def test_a_change_mapped_only_to_another_invariant_is_outside_selection_not_unregistered(
    fixture: DiffFixture,
) -> None:
    """A path another invariant realizes is attributed *outside* the selected subject.

    The review is opened for the retry obligation, and the rewritten ``src/batch.py`` is realized by
    that obligation's sibling in the same family. The comparison's own selected-claim calculation
    would leave it to the unattributed gap -- "globally unregistered" -- merely because a different
    subject was selected, which is the reading this requirement refuses. It is attributed, its link
    is established as exclusively outside the selected subject (both snapshots were completely
    inspected), and the subject's own links are labelled as such.
    """

    result = run_diff(fixture, diff_seed(fixture))

    assert result.state == "page", result.refusal
    expansion = result.expansion
    assert expansion is not None
    partition = expansion.attribution
    assert partition is not None
    by_path = {entry.path: entry for entry in partition.paths}

    outside = by_path[fixture.changed_source_path]
    assert outside.bucket == "attributed"
    assert outside.link == "outside_selection_complete"
    assert fixture.changed_source_path not in set(expansion.unattributed_changed_paths)
    assert fixture.changed_source_path not in set(expansion.unknown_attribution_changed_paths)

    # The removed realization of the selected subject is a link to *it*, on the side that held it.
    removed = by_path[fixture.baseline_only_path]
    assert removed.bucket == "attributed"
    assert removed.link == "selected_subject"
    assert removed.mapped_sides == ("before",)
    added = by_path[fixture.candidate_only_path]
    assert added.link == "selected_subject"
    assert added.mapped_sides == ("after",)
    assert set(partition.selected_subject_paths) == {
        fixture.baseline_only_path,
        fixture.candidate_only_path,
    } | {path for path, entry in by_path.items() if entry.link == "selected_subject"}


def test_a_family_subject_counts_its_members_own_links_as_selected_attribution(
    fixture: DiffFixture,
) -> None:
    """A family subject's own scope is the membership its rows record, read per side.

    The subject here is the family that directly contains both the retry obligation and its sibling,
    so the rewritten ``src/batch.py`` -- which is realized by the sibling -- is a link to the
    *selected* subject and not an outside one. The candidate's newly authored realization is a
    different fact and is reported as the record states it: its owning revision is the successor the
    candidate authored, which the family's recorded membership does not name, so that path is
    attributed and established as exclusively outside the selected subject's own scope. Nothing here
    infers membership from a successor relationship or a label; the family's rows answer it.
    """

    family_id = fixture.before.fixture.family.family_id

    result = run_diff(fixture, FamilyIdentitySeed(family_id=family_id))

    assert result.state == "page", result.refusal
    expansion = result.expansion
    assert expansion is not None
    partition = expansion.attribution
    assert partition is not None
    by_path = {entry.path: entry for entry in partition.paths}
    assert partition.subject_scope_complete is True
    assert by_path[fixture.changed_source_path].link == "selected_subject"
    assert by_path[fixture.baseline_only_path].link == "selected_subject"
    assert by_path[fixture.candidate_only_path].link == "outside_selection_complete"
    assert set(partition.selected_subject_paths) == {
        fixture.changed_source_path,
        fixture.baseline_only_path,
        fixture.before.fixture.integration.path,
    }


def test_a_registered_mapping_that_did_not_resolve_does_not_establish_attribution() -> None:
    """A purported mapping that never resolved to its path is carried, and is not an attribution.

    Three facts are measured at once, and each is a way a count could lie. A claim registered at the
    path whose anchor did not resolve does **not** put the path in the attributed bucket -- it is
    counted as an unresolved purported mapping beside the bucket instead of being promoted. A path
    two links name is one path in the totals. And with every required snapshot completely inspected,
    a path no valid mapping reaches is *confirmed* unregistered rather than left undetermined.
    """

    mapped = "src/mapped.py"
    unresolved = "src/unresolved.py"
    duplicate = "src/duplicate.py"
    orphan = "src/orphan.py"
    facts = (
        MappingFact(
            side="before",
            path=mapped,
            claim_id="claim-a",
            resolution="exact_recorded_blob",
            resolved=True,
            subject_link=True,
        ),
        MappingFact(
            side="after",
            path=duplicate,
            claim_id="claim-b",
            resolution="recorded_blob_mismatch",
            resolved=False,
        ),
        MappingFact(
            side="after",
            path=duplicate,
            claim_id="claim-c",
            resolution="exact_recorded_blob",
            resolved=True,
        ),
        MappingFact(
            side="before",
            path=unresolved,
            claim_id="claim-d",
            resolution="path_absent",
            resolved=False,
        ),
        MappingFact(
            side="after",
            path=unresolved,
            claim_id="claim-e",
            resolution="unsupported_locator",
            resolved=False,
        ),
    )

    partition = partition_attribution(
        _observed(mapped, unresolved, duplicate, orphan),
        facts,
        _inspected_sides(),
        subject_selected=True,
        subject_scope_complete=True,
    )

    by_path = {entry.path: entry for entry in partition.paths}
    assert by_path[mapped].bucket == "attributed"
    assert by_path[mapped].link == "selected_subject"
    # The duplicate path is attributed by the one *exact* link and the stale link beside it is counted
    # as an unresolved purported mapping, so the bucket is neither inflated nor lost by it.
    assert by_path[duplicate].bucket == "attributed"
    assert by_path[duplicate].link == "outside_selection_complete"
    assert by_path[duplicate].mapped_sides == ("after",)
    assert by_path[duplicate].unresolved_mapping_count == 1
    assert by_path[unresolved].bucket == "confirmed_unregistered"
    assert by_path[unresolved].unresolved_mapping_count == 2
    assert by_path[orphan].bucket == "confirmed_unregistered"
    assert partition.changed_total == 4
    assert (
        partition.attributed_total,
        partition.confirmed_unregistered_total,
        partition.unknown_attribution_total,
    ) == (2, 2, 0)


def test_a_stale_or_unresolvable_mapping_does_not_establish_attribution(
    fixture: DiffFixture,
) -> None:
    """A registered mapping whose evidence is stale is carried unresolved, and attributes nothing.

    Two claims are authored on the candidate snapshot at the added path the fixture leaves unmapped,
    and each is a producer of the shipped *stale* resolution rather than of a missing path:

    * one records a blob identity **no compared tree holds**, so the bytes at the recorded path are not
      the recorded bytes;
    * one records the blob the candidate tree **really holds**, but binds it with a symbol locator
      those bytes do not define -- the case that falsifies "a mismatch is only ever a content fact",
      because there the content matches exactly and only the locator failed to resolve.

    Both are measured through the shipped anchor reader before the partition is read, so the case says
    which resolution it is testing rather than assuming one. The partition must then report the path as
    *confirmed unregistered* -- not attributed, and not carrying a selected-subject link -- with both
    purported mappings counted as unresolved, while the fixture's own conforming mapping (the
    baseline's exact `src/batch.py` claim) keeps its attribution and its stale candidate-side twins are
    counted beside it rather than promoting it.
    """

    real_blob = fixture.after.git_blobs[fixture.unattributed_path]
    _author_candidate_claim(
        fixture,
        path=fixture.unattributed_path,
        recorded_blob="0" * 40,
        locator=FileLocator(),
    )
    _author_candidate_claim(
        fixture,
        path=fixture.unattributed_path,
        recorded_blob=real_blob,
        locator=SymbolLocator(language="python", qualified_name="icr_r04_symbol_never_defined"),
    )
    resolutions = _candidate_claim_resolutions(fixture, fixture.unattributed_path)
    assert resolutions == ["recorded_blob_mismatch", "recorded_blob_mismatch"], (
        f"the case must exercise the stale resolution, not a missing path; measured {resolutions}"
    )

    result = run_diff(fixture, diff_seed(fixture))

    assert result.state == "page", result.refusal
    expansion = result.expansion
    assert expansion is not None
    partition = expansion.attribution
    assert partition is not None
    by_path = {entry.path: entry for entry in partition.paths}
    stale = by_path[fixture.unattributed_path]
    assert stale.bucket == "confirmed_unregistered"
    assert stale.link is None
    assert stale.mapped_sides == ()
    assert stale.unresolved_mapping_count == 2
    assert fixture.unattributed_path in set(expansion.unattributed_changed_paths)
    assert fixture.unattributed_path not in set(partition.selected_subject_paths)
    # The conforming mapping is unchanged by the strict reading: the baseline holds the recorded bytes
    # at the rewritten path, so the path stays attributed -- by that side only, with its two stale
    # candidate-side claims counted rather than promoted.
    rewritten = by_path[fixture.changed_source_path]
    assert rewritten.bucket == "attributed"
    assert rewritten.mapped_sides == ("before",)
    assert rewritten.unresolved_mapping_count == 2
    assert (partition.attributed_total, partition.confirmed_unregistered_total) == (4, 1)


def _author_candidate_claim(
    fixture: DiffFixture,
    *,
    path: str,
    recorded_blob: str,
    locator: FileLocator | SymbolLocator,
) -> str:
    """Author one realization claim on the candidate snapshot, exactly as the write plane does."""

    claim_id = str(uuid4())
    store = open_knowledge_store(fixture.after.database_path, fixture.repository_id)
    try:
        created = realizations.create_realization_claim(
            store,
            RealizationClaimRequest(
                repository_id=fixture.repository_id,
                claim=RealizationClaimDraft(
                    claim_id=claim_id,
                    invariant_revision_id=fixture.revised_revision_id,
                    role="enforcement",
                    rationale="ICR-R04: a mapping whose recorded evidence is stale",
                ),
                anchor=NewAnchor(
                    anchor=SourceAnchorDraft(
                        anchor_id=uuid4(),
                        path=path,
                        source_identity=GitBlobIdentity(object_id=recorded_blob),
                        locator=locator,
                    )
                ),
                provenance=fixture.after.fixture.authorship,
            ),
        )
        assert created.state == "created", created.refusal
    finally:
        store.close()
    return claim_id


def _candidate_claim_resolutions(fixture: DiffFixture, path: str) -> list[str]:
    """The shipped anchor reader's own resolution for every candidate claim at one path.

    It is read through the memory layer's lookup and observation owners rather than through the
    partition, so the case's premise ("this is the stale resolution") is an independent measurement.
    """

    store = open_knowledge_store(fixture.after.database_path, fixture.repository_id)
    try:
        rows = fetch_realizations_at_path(store.connection, fixture.repository_id, path)
    finally:
        store.close()
    return [
        observe_anchor(
            row, repository_root=fixture.after.git_root, tree_id=fixture.after_tree_id
        ).resolution
        for row in rows
    ]


def test_one_uninspected_snapshot_turns_every_unmapped_change_into_undetermined_attribution() -> (
    None
):
    """The precedence rule with one side unread: a positive mapping stands, a negative one does not.

    A mapping the readable snapshot resolves keeps its path attributed, and the *other* side's silence
    is labelled rather than converted into a conclusion: every path without a resolved mapping is of
    undetermined attribution, because a snapshot nobody inspected is not one that registered nothing.
    A link that is only known outside the selected subject degrades the same way -- its membership in
    the subject's own scope is not established by a comparison that did not complete.
    """

    mapped = "src/mapped.py"
    outside = "src/outside.py"
    orphan = "src/orphan.py"
    facts = (
        MappingFact(
            side="after",
            path=mapped,
            claim_id="claim-a",
            resolution="exact_recorded_blob",
            resolved=True,
            subject_link=True,
        ),
        MappingFact(
            side="after",
            path=outside,
            claim_id="claim-b",
            resolution="exact_recorded_blob",
            resolved=True,
        ),
    )
    sides = (
        SideInspection(
            side="before",
            state="unavailable",
            registered_mapping_count=None,
            detail="injected: the before snapshot was not readable",
        ),
        *_inspected_sides()[1:],
    )

    partition = partition_attribution(
        _observed(mapped, outside, orphan),
        facts,
        sides,
        subject_selected=True,
        subject_scope_complete=False,
    )

    by_path = {entry.path: entry for entry in partition.paths}
    assert partition.complete is False
    assert partition.subject_scope_complete is False
    assert by_path[mapped].bucket == "attributed"
    assert by_path[mapped].link == "selected_subject"
    assert by_path[outside].bucket == "attributed"
    assert by_path[outside].link == "outside_selection_membership_unknown"
    assert by_path[orphan].bucket == "unknown_attribution"
    assert (
        partition.attributed_total,
        partition.confirmed_unregistered_total,
        partition.unknown_attribution_total,
    ) == (2, 0, 1)


def test_a_legitimately_empty_side_counts_as_completely_inspected_for_absence() -> None:
    """A recorded empty first generation is a complete inspection, so absence stays a conclusion.

    The distinction the packet draws is between a side that was read and held nothing and a side
    nobody read, and only the first supports "no valid registered attribution". A side the partition
    was told is a legitimately known-empty one is the first, so the unmapped change is confirmed
    unregistered -- and the value names that side's state rather than collapsing it into "we looked".
    """

    orphan = "src/orphan.py"

    partition = partition_attribution(
        _observed(orphan),
        (),
        (
            SideInspection(
                side="before",
                state="known_empty",
                registered_mapping_count=0,
                detail="injected: an identified empty first generation",
            ),
            *_inspected_sides()[1:],
        ),
        subject_selected=False,
        subject_scope_complete=True,
    )

    assert partition.state == "measured"
    assert partition.complete is True
    assert partition.confirmed_unregistered_paths == (orphan,)
    assert partition.unknown_attribution_paths == ()
    assert partition.subject_scope_complete is None, (
        "a review that selected no subject states no subject scope, rather than claiming one was "
        "completely inspected"
    )
    assert [entry.state for entry in partition.sides] == ["known_empty", "inspected"]


def test_an_unavailable_partition_states_no_total_and_never_a_measured_zero() -> None:
    """A source measurement that was not made has no denominator, so it reports no count at all.

    This is the first half of the finding's failure: an unavailable scan used to become "0
    unattributed", a measured zero nothing measured. The value now says which measurement is missing
    and carries no number, and the model refuses a value that carried one beside an empty path list.
    """

    observed = TreePaths(available=False, detail="injected: the two trees were never compared")

    partition = unavailable_attribution(observed, "the review measured no trees")

    assert partition.state == "unavailable"
    assert partition.paths == ()
    assert partition.changed_total is None
    assert partition.attributed_total is None
    assert partition.confirmed_unregistered_total is None
    assert partition.unknown_attribution_total is None
    assert partition.complete is None
    assert "the two trees were never compared" in partition.detail
    # The refusal is structural, so no caller can build the values this case exists to forbid: an
    # unavailable partition that carries a number beside its empty path list is refused at
    # construction, and so is a "measured" one whose buckets do not sum to its denominator.
    with pytest.raises(ValidationError):
        SourceAttribution(state="unavailable", changed_total=0, detail="a zero nothing measured")
    with pytest.raises(ValidationError):
        SourceAttribution(state="measured", changed_total=3, detail="buckets that do not sum")


def test_a_partial_observation_states_the_scope_of_its_own_denominator() -> None:
    """A denominator that had to drop a path says so where its counts are read.

    Attribution is counted over the paths a name can carry, so an observation that could not carry one
    states its scope in its own detail: the total is the *carriable* population, the number left out is
    named, and the reason travels with it. A consumer that did not open the inventory beside the value
    must not be able to read a smaller total as the whole population. The complete case says the plain
    fact instead, so the two read differently rather than one of them reading as silence.
    """

    complete = partition_attribution(
        _observed("src/one.py"),
        (),
        _inspected_sides(),
        subject_selected=False,
        subject_scope_complete=True,
    )
    assert complete.changed_total == 1
    assert "whole measured change population" in complete.detail
    assert "CARRIABLE" not in complete.detail

    partial = partition_attribution(
        TreePaths(
            available=True,
            paths=("src/one.py",),
            unrepresentable=(
                TreeChange(
                    path="src/caf\udcff-latin1.py",
                    status="added",
                    content="text",
                ),
            ),
            partial=True,
            detail="one changed path could not be carried as a name in this vocabulary",
        ),
        (),
        _inspected_sides(),
        subject_selected=False,
        subject_scope_complete=True,
    )
    assert partial.changed_total == 1
    assert "CARRIABLE measured change population" in partial.detail
    assert "1 further changed path" in partial.detail
    assert "one changed path could not be carried as a name" in partial.detail


def _observed(*paths: str) -> TreePaths:
    """One complete observation of the named changed paths, for the arithmetic cases."""

    return TreePaths(available=True, paths=tuple(paths))


def _inspected_sides() -> tuple[SideInspection, SideInspection]:
    """Two completely inspected snapshots, for the arithmetic cases that are not about that."""

    return (
        SideInspection(
            side="before",
            state="inspected",
            registered_mapping_count=0,
            detail="injected: the before snapshot was scanned to completion",
        ),
        SideInspection(
            side="after",
            state="inspected",
            registered_mapping_count=0,
            detail="injected: the after snapshot was scanned to completion",
        ),
    )
