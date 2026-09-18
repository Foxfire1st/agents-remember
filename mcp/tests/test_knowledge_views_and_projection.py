"""``KS-R20@v1`` §2, §3 and §5 as behaviour: the classification, the bounding and the vault safety.

Every case names the failure it catches, and the four that carry the leaf are the ordering
differential, the classification completeness scan, the authored-versus-mechanical separation, and
the vault-safety scenario in which two of the checkpoints protect files the substrate never wrote.

The cases run without a database. The view vocabulary, the closed rule registry, the ordering pass
and the projection writer are all pure functions of their inputs -- which is exactly why the
classification rule can be tested without a store, and why a renderer that opened one would not
typecheck against the port in the first place.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from agents_remember.memory.knowledge.managed_projection import (
    ManagedProjectionWriter,
    ProjectionHooks,
)
from agents_remember.models.knowledge.classification import (
    AUTHORED_CLASS,
    CLASSIFICATION_CLASSES,
    MECHANICAL_CLASS,
    MECHANICAL_RULE_REGISTRY_VERSION,
    MECHANICAL_RULES,
    ORDERING_INPUTS,
    MechanicalRuleNotRegistered,
    Provenance,
    authored_provenance,
    mechanical_provenance,
    mechanical_rule,
    mechanical_rules_for,
)
from agents_remember.models.knowledge.projection_manifest import (
    PROJECTION_MANIFEST_NAME,
    STAGING_DIRECTORY_NAME,
    DestinationProfile,
    ManagedOutput,
    ProjectionManifest,
    ProjectionPlan,
    RenderedOutput,
    detect_destination_collisions,
    require_confined_relative_path,
)
from agents_remember.models.knowledge.read import KnowledgeReadSnapshot
from agents_remember.models.knowledge.view import (
    CurationQueueRow,
    MachineWorkItem,
    NoConsequenceStatement,
    OrderedPosition,
    SourceContextView,
    SubjectRef,
    ViewCompleteness,
    ViewCounts,
    ViewRefusal,
    ViewScope,
    continuation_for,
    ordering_position,
    require_admitted_ordering_input,
    require_continuation_snapshot,
    view_counts,
)

SNAPSHOT = KnowledgeReadSnapshot(
    repository_id="11111111-1111-1111-1111-111111111111",
    schema_version="ar-knowledge-sqlite/v8",
    logical_digest="a" * 64,
    context_digest="b" * 64,
)


def _completeness(snapshot: KnowledgeReadSnapshot = SNAPSHOT) -> ViewCompleteness:
    return ViewCompleteness(
        complete_within_declared_scope=True,
        scope=ViewScope(
            snapshot_logical_digest=snapshot.logical_digest,
            recorded_graph="recorded",
            traversal_policy="knowledge-view-selection/v1",
        ),
    )


def _output(relative_path: str, identity: str, text: str) -> RenderedOutput:
    return RenderedOutput(
        destination_relative_path=relative_path,
        stable_identity=identity,
        record_kind="invariant_revision",
        format="markdown",
        source_snapshot="a" * 64,
        renderer_version="knowledge-view-renderer/1",
        text=text,
    )


def _profile(root: Path) -> DestinationProfile:
    return DestinationProfile(
        profile_id="local",
        destination_root=str(root),
        formats=("markdown",),
        renderer_version="knowledge-view-renderer/1",
    )


# ---------------------------------------------------------------------------
# The closed provenance class.
# ---------------------------------------------------------------------------


def test_the_class_set_is_closed_at_two_members() -> None:
    """A third class would be the unclassifiable value the packet forbids wearing a name."""

    assert CLASSIFICATION_CLASSES == ("authored", "mechanical")


def test_an_authored_classification_carries_its_author_and_cannot_cite_a_rule() -> None:
    """Requirement 2.3: an authored determination never cites a mechanical rule as its reason."""

    provenance = authored_provenance("agent-7", "rewording only; the obligation is unchanged")
    assert provenance.provenance_class == AUTHORED_CLASS
    assert provenance.authored is not None
    assert provenance.authored.author_ref == "agent-7"
    with pytest.raises(ValueError):
        Provenance(
            provenance_class=AUTHORED_CLASS,
            authored=provenance.authored,
            rule_id="ordering.declared-tiebreak",
            rule_version=1,
        )


def test_a_mechanical_classification_names_its_rule_and_has_no_author() -> None:
    """Requirement 2.3: the mechanical determination never writes an authored record."""

    provenance = mechanical_provenance("ordering.declared-tiebreak", 1)
    assert provenance.provenance_class == MECHANICAL_CLASS
    assert provenance.rule_id == "ordering.declared-tiebreak"
    assert provenance.authored is None
    with pytest.raises(ValueError):
        Provenance(
            provenance_class=MECHANICAL_CLASS,
            authored=authored_provenance("a", "b").authored,
            rule_id="ordering.declared-tiebreak",
            rule_version=1,
        )


def test_a_class_without_its_evidence_is_refused_rather_than_defaulted() -> None:
    """A value with no author and no rule has no class here, so it cannot be emitted as classified."""

    with pytest.raises(ValueError):
        Provenance(provenance_class=AUTHORED_CLASS)
    with pytest.raises(ValueError):
        Provenance(provenance_class=MECHANICAL_CLASS)


def test_an_unregistered_rule_cannot_produce_a_classification() -> None:
    """Requirement 2.2: a rule not in the registry cannot produce a classification."""

    with pytest.raises(MechanicalRuleNotRegistered):
        mechanical_provenance("ordering.invented-by-a-renderer", 1)
    with pytest.raises(MechanicalRuleNotRegistered):
        mechanical_rule("ordering.declared-tiebreak", 999)


def test_the_registry_admits_one_rule_per_ordering_input() -> None:
    """Expected Evidence: at least one rule per admitted ordering input, and the closure holds."""

    declared = {rule.ordering_input for rule in mechanical_rules_for("ordering")}
    assert declared == set(ORDERING_INPUTS)
    assert MECHANICAL_RULE_REGISTRY_VERSION == 1
    assert all(rule.reads for rule in MECHANICAL_RULES)


def test_no_registered_rule_reads_a_name_a_path_or_a_score() -> None:
    """The four forbidden ordering sources are absent from every registered rule's declared reads."""

    forbidden = ("name", "symbol", "prefix", "extension", "depth", "score", "rank", "weight")
    for rule in MECHANICAL_RULES:
        for read in rule.reads:
            assert not any(token in read for token in forbidden), (rule.rule_id, read)


# ---------------------------------------------------------------------------
# Ordering and the refusals around it.
# ---------------------------------------------------------------------------


def test_an_unadmitted_ordering_input_is_refused_rather_than_defaulted() -> None:
    """Failure state: ``unadmitted_ordering_input``, with no fallback order and no rows."""

    refusal = require_admitted_ordering_input("by_symbol_name_length")
    assert isinstance(refusal, ViewRefusal)
    assert refusal.code == "unadmitted_ordering_input"
    assert refusal.observed == "by_symbol_name_length"
    assert require_admitted_ordering_input("declared_priority") is None


def test_every_admitted_position_names_the_declared_tiebreak_rule() -> None:
    """A position that named no registered tiebreak could not be reproduced across two runs."""

    position = ordering_position(
        1, "declared_priority", authored_provenance("curator-2", "highest")
    )
    assert position.position == 1
    assert position.tiebreak_rule_id == "ordering.declared-tiebreak"
    assert position.provenance.provenance_class == AUTHORED_CLASS


def test_a_position_cannot_name_an_unregistered_rule() -> None:
    """An inline comparator cannot acquire a position by naming a rule the registry does not carry."""

    with pytest.raises(MechanicalRuleNotRegistered):
        OrderedPosition(
            position=1,
            ordering_input="stable_ordering",
            provenance=mechanical_provenance("ordering.declared-tiebreak", 1),
            tiebreak_rule_id="ordering.made-up",
            tiebreak_rule_version=1,
        )


# ---------------------------------------------------------------------------
# Counts, continuation and the bounding honesty rule.
# ---------------------------------------------------------------------------


def test_a_payload_with_rows_remaining_must_carry_a_continuation() -> None:
    """Requirement 3.3: a bounded response never presents its first page as the whole scope."""

    counts = view_counts(
        registered_realizations=2, registered_families=1, rows_returned=2, rows_remaining=3
    )
    with pytest.raises(ValueError):
        SourceContextView(
            snapshot=SNAPSHOT,
            counts=counts,
            completeness=_completeness(),
            renderer_version="knowledge-view-renderer/1",
        )


def test_a_complete_payload_must_not_carry_a_continuation() -> None:
    """The two facts are one statement, so a complete page with a token is refused too."""

    counts = view_counts(
        registered_realizations=2, registered_families=1, rows_returned=2, rows_remaining=0
    )
    with pytest.raises(ValueError):
        SourceContextView(
            snapshot=SNAPSHOT,
            counts=counts,
            completeness=_completeness(),
            continuation=continuation_for(view="source_context", snapshot=SNAPSHOT, position=2),
            renderer_version="knowledge-view-renderer/1",
        )


def test_a_continuation_presented_against_another_snapshot_is_refused_with_both_named() -> None:
    """Failure state: the view does not silently re-resolve and the caller receives no page."""

    other = SNAPSHOT.model_copy(update={"logical_digest": "c" * 64})
    continuation = continuation_for(view="review_matrix", snapshot=SNAPSHOT, position=4)
    assert require_continuation_snapshot(continuation, SNAPSHOT) is None
    refusal = require_continuation_snapshot(continuation, other)
    assert refusal is not None
    assert refusal.code == "continuation_binding_mismatch"
    assert refusal.expected == "a" * 64
    assert refusal.observed == "c" * 64


def test_a_quantity_with_no_meaning_for_a_view_says_so_instead_of_reporting_zero() -> None:
    """Every required quantity is present, and an absent measurement is stated, never a zero."""

    counts = view_counts(
        registered_realizations=None, registered_families=None, rows_returned=0, rows_remaining=0
    )
    assert counts.registered_realizations.state == "not_applicable"
    assert counts.registered_realizations.value is None
    assert counts.registered_realizations.reason
    assert counts.rows_returned.value == 0
    with pytest.raises(ValueError):
        ViewCounts.model_validate(
            {
                **counts.model_dump(mode="json"),
                "facets_omitted": {"state": "not_applicable"},
            }
        )


def test_the_curation_queue_keeps_a_work_item_free_of_any_curator_judgement() -> None:
    """Requirement 1.6 is a shape obligation: the two attributions live in two records."""

    item = MachineWorkItem(
        work_item_id="w-1",
        condition_code="member_source_diverged",
        condition_vocabulary_version="detection-condition/v1",
        matched_facts=("claim-4",),
    )
    body = item.model_dump()
    assert "disposition" not in body
    assert "rationale" not in body
    assert "author_ref" not in body
    row = CurationQueueRow(
        item=item,
        order=ordering_position(
            1, "explicit_trigger_rule", mechanical_provenance("ordering.trigger-rule", 1)
        ),
    )
    assert row.disposition is None


def test_a_no_consequence_statement_is_refused_without_the_class_that_produced_it() -> None:
    """Requirement 2.3: a mechanical determination writes no authored record to name."""

    subject = SubjectRef(record_kind="invariant_revision", record_id="INV-014")
    mechanical = NoConsequenceStatement(
        subject=subject,
        detail="whitespace only",
        provenance=mechanical_provenance("consequence.anchor-text-whitespace-only", 1),
    )
    assert mechanical.claim_ref is None
    with pytest.raises(ValueError):
        NoConsequenceStatement(
            subject=subject,
            detail="whitespace only",
            provenance=mechanical_provenance("consequence.anchor-text-whitespace-only", 1),
            claim_ref="NC-7",
        )


def test_an_authored_no_consequence_statement_names_the_stored_claim_it_is() -> None:
    """Requirement 2.3's other half: the authored determination is a stored, attributable record."""

    statement = NoConsequenceStatement(
        subject=SubjectRef(record_kind="invariant_revision", record_id="INV-031"),
        detail="rewording only",
        provenance=authored_provenance("agent-7", "the obligation is unchanged in every branch"),
        claim_ref="NC-7",
    )
    assert statement.claim_ref == "NC-7"
    assert statement.provenance.authored is not None


# ---------------------------------------------------------------------------
# Confinement and collisions, without a filesystem.
# ---------------------------------------------------------------------------


def test_a_path_that_is_not_purely_destination_relative_is_refused() -> None:
    """Requirement 5.2's syntactic half, before any real-path resolution runs."""

    for offending in (
        "/etc/passwd.md",
        "../escape.md",
        "a/../../b.md",
        "knowledge//a.md",
        f"{STAGING_DIRECTORY_NAME}/x.md",
        "",
    ):
        refusal = require_confined_relative_path(offending)
        assert refusal is not None, offending
        assert refusal.code == "destination_escape"
    assert require_confined_relative_path("invariants/INV-014.md") is None


def test_a_case_collision_names_both_paths_and_both_records() -> None:
    """Requirement 5.3: neither is written, and no disambiguating suffix is invented."""

    collisions = detect_destination_collisions(
        (
            _output("invariants/INV-014.md", "INV-014", "a"),
            _output("invariants/inv-014.md", "INV-014-b", "b"),
        )
    )
    assert len(collisions) == 1
    assert collisions[0].canonical_form == "invariants/inv-014.md"
    assert set(collisions[0].stable_identities) == {"INV-014", "INV-014-b"}
    assert detect_destination_collisions((_output("a.md", "A", "a"),)) == ()


# ---------------------------------------------------------------------------
# The vault-safety writer, over a real temporary destination.
# ---------------------------------------------------------------------------


def test_the_first_projection_writes_a_generation_one_manifest(tmp_path: Path) -> None:
    """The manifest's five recorded values are what every later unchanged test reads."""

    root = tmp_path / "vault"
    report = ManagedProjectionWriter().write(
        ProjectionPlan(
            destination=_profile(root),
            outputs=(_output("invariants/INV-014.md", "INV-014", "# A\n"),),
        )
    )
    assert report.state == "projected"
    assert report.manifest_generation == 1
    manifest = json.loads((root / PROJECTION_MANIFEST_NAME).read_text(encoding="utf-8"))
    entry = manifest["outputs"][0]
    assert entry["destination_relative_path"] == "invariants/INV-014.md"
    assert entry["stable_identity"] == "INV-014"
    assert entry["source_snapshot"] == "a" * 64
    assert entry["renderer_version"] == "knowledge-view-renderer/1"
    assert entry["digest"] and entry["digest_algorithm"] == "sha256"


def test_an_unchanged_orphan_is_removed_and_a_modified_one_is_retained(tmp_path: Path) -> None:
    """Requirement 5.6's two halves: the unchanged test needs the manifest *and* the bytes."""

    root = tmp_path / "vault"
    writer = ManagedProjectionWriter()
    writer.write(
        ProjectionPlan(
            destination=_profile(root),
            outputs=(
                _output("families/GUA-003.md", "GUA-003", "# kept\n"),
                _output("incidents/INC-009.md", "INC-009", "# edited\n"),
            ),
        )
    )
    (root / "incidents/INC-009.md").write_text("# user edit\n", encoding="utf-8")
    report = writer.write(
        ProjectionPlan(
            destination=_profile(root),
            outputs=(_output("invariants/INV-014.md", "INV-014", "# new\n"),),
        )
    )
    assert not (root / "families/GUA-003.md").exists()
    assert (root / "incidents/INC-009.md").read_text(encoding="utf-8") == "# user edit\n"
    retained = {entry.destination_relative_path: entry.reason for entry in report.retained}
    assert retained == {"incidents/INC-009.md": "edited-since-last-projection"}
    states = {outcome.destination_relative_path: outcome.state for outcome in report.outcomes}
    assert states["families/GUA-003.md"] == "removed"
    assert states["incidents/INC-009.md"] == "retained-with-reason"


def test_an_externally_edited_output_is_reported_and_never_overwritten(tmp_path: Path) -> None:
    """Requirement 5.8: reported with its kind, preserved, and the remaining outputs complete."""

    root = tmp_path / "vault"
    writer = ManagedProjectionWriter()
    writer.write(
        ProjectionPlan(
            destination=_profile(root),
            outputs=(_output("invariants/INV-014.md", "INV-014", "# A\n"),),
        )
    )
    (root / "invariants/INV-014.md").write_text("# edited by the user\n", encoding="utf-8")
    report = writer.write(
        ProjectionPlan(
            destination=_profile(root),
            outputs=(
                _output("invariants/INV-014.md", "INV-014", "# A\n"),
                _output("families/GUA-003.md", "GUA-003", "# B\n"),
            ),
        )
    )
    assert (root / "invariants/INV-014.md").read_text(encoding="utf-8") == "# edited by the user\n"
    assert [entry.kind for entry in report.discrepancies] == ["modified"]
    assert (root / "families/GUA-003.md").exists()
    manifest = json.loads((root / PROJECTION_MANIFEST_NAME).read_text(encoding="utf-8"))
    recorded = next(
        entry
        for entry in manifest["outputs"]
        if entry["destination_relative_path"] == "invariants/INV-014.md"
    )
    # The prior digest is kept, so the next run detects the same edit rather than adopting it.
    assert recorded["digest"] == ManagedOutput.model_validate(recorded).digest
    third = writer.write(
        ProjectionPlan(
            destination=_profile(root),
            outputs=(_output("invariants/INV-014.md", "INV-014", "# A\n"),),
        )
    )
    assert [entry.kind for entry in third.discrepancies] == ["modified"]


def test_an_explicit_per_path_authorization_is_the_only_route_to_an_overwrite(
    tmp_path: Path,
) -> None:
    """Requirement 5.8: the authorization is per path and is recorded in the manifest entry."""

    root = tmp_path / "vault"
    writer = ManagedProjectionWriter()
    writer.write(
        ProjectionPlan(
            destination=_profile(root),
            outputs=(
                _output("invariants/INV-014.md", "INV-014", "# A\n"),
                _output("families/GUA-003.md", "GUA-003", "# B\n"),
            ),
        )
    )
    (root / "invariants/INV-014.md").write_text("# edited\n", encoding="utf-8")
    (root / "families/GUA-003.md").write_text("# edited too\n", encoding="utf-8")
    report = writer.write(
        ProjectionPlan(
            destination=_profile(root),
            outputs=(
                _output("invariants/INV-014.md", "INV-014", "# A\n"),
                _output("families/GUA-003.md", "GUA-003", "# B\n"),
            ),
            authorized_overwrites=("invariants/INV-014.md",),
        )
    )
    assert (root / "invariants/INV-014.md").read_text(encoding="utf-8") == "# A\n"
    assert (root / "families/GUA-003.md").read_text(encoding="utf-8") == "# edited too\n"
    authorized = {
        entry.destination_relative_path: entry.authorized_overwrite
        for entry in report.manifest.outputs
    }
    assert authorized == {"invariants/INV-014.md": True, "families/GUA-003.md": False}


def test_an_escaping_path_refuses_the_whole_plan_and_writes_nothing(tmp_path: Path) -> None:
    """Requirement 5.2: the refusal names the path and the resolved root, and nothing is staged."""

    root = tmp_path / "vault"
    report = ManagedProjectionWriter().write(
        ProjectionPlan(
            destination=_profile(root),
            outputs=(
                _output("invariants/INV-014.md", "INV-014", "# A\n"),
                _output("../escaped.md", "X", "# X\n"),
            ),
        )
    )
    assert report.state == "refused"
    assert report.refusal is not None
    assert report.refusal.code == "destination_escape"
    assert report.refusal.resolved_root == str(Path(os.path.realpath(root)))
    assert not (root / "invariants").exists()
    assert not (root / PROJECTION_MANIFEST_NAME).exists()


def test_a_collision_refuses_the_whole_plan_before_either_output_is_written(tmp_path: Path) -> None:
    """Requirement 5.3: reported before either is written, with both source records named."""

    root = tmp_path / "vault"
    report = ManagedProjectionWriter().write(
        ProjectionPlan(
            destination=_profile(root),
            outputs=(
                _output("invariants/INV-014.md", "INV-014", "# one\n"),
                _output("invariants/inv-014.md", "INV-014-other", "# two\n"),
            ),
        )
    )
    assert report.state == "refused"
    assert report.refusal is not None
    assert report.refusal.code == "destination_collision"
    assert "INV-014" in (report.refusal.observed or "")
    assert not (root / "invariants").exists()


def test_a_symlinked_destination_entry_is_not_followed_and_the_rest_continues(
    tmp_path: Path,
) -> None:
    """Requirement 5.4: reported, left alone, and one hostile entry does not block the others."""

    root = tmp_path / "vault"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.symlink(elsewhere, root / "knowledge", target_is_directory=True)
    report = ManagedProjectionWriter().write(
        ProjectionPlan(
            destination=_profile(root),
            outputs=(
                _output("knowledge/latest.md", "L", "# L\n"),
                _output("families/GUA-003.md", "GUA-003", "# B\n"),
            ),
        )
    )
    assert report.state == "projected"
    assert not (elsewhere / "latest.md").exists()
    assert (root / "families/GUA-003.md").exists()
    states = {outcome.destination_relative_path: outcome.state for outcome in report.outcomes}
    assert states["knowledge/latest.md"] == "reported"


def test_an_interruption_leaves_the_destination_exactly_as_it_was(tmp_path: Path) -> None:
    """Requirement 5.5: publication is a rename, so a failure partway changes nothing.

    The interruption is induced by the writer's own ``before_publish`` hook, which runs after every
    output is staged and before the first rename. The packet's Open Truth Gap records that whether
    this checkpoint was deterministically inducible was unverified; this case is the answer.
    """

    root = tmp_path / "vault"
    ManagedProjectionWriter().write(
        ProjectionPlan(
            destination=_profile(root),
            outputs=(_output("families/GUA-003.md", "GUA-003", "# before\n"),),
        )
    )
    before = _tree_snapshot(root)

    def _interrupt() -> None:
        raise RuntimeError("interrupted between staging and publication")

    writer = ManagedProjectionWriter(ProjectionHooks(before_publish=_interrupt))
    with pytest.raises(RuntimeError):
        writer.write(
            ProjectionPlan(
                destination=_profile(root),
                outputs=(
                    _output("families/GUA-003.md", "GUA-003", "# after\n"),
                    _output("invariants/INV-014.md", "INV-014", "# new\n"),
                ),
            )
        )
    assert _tree_snapshot(root) == before
    assert (root / "families/GUA-003.md").read_text(encoding="utf-8") == "# before\n"


def test_the_destination_is_never_swept_and_unlisted_files_are_untouched(tmp_path: Path) -> None:
    """Requirement 5.7: a file the manifest does not list is not the substrate's to remove."""

    root = tmp_path / "vault"
    (root / "user-notes").mkdir(parents=True)
    (root / "user-notes" / "note.md").write_text("mine\n", encoding="utf-8")
    writer = ManagedProjectionWriter()
    writer.write(
        ProjectionPlan(
            destination=_profile(root),
            outputs=(_output("invariants/INV-014.md", "INV-014", "# A\n"),),
        )
    )
    report = writer.write(ProjectionPlan(destination=_profile(root), outputs=()))
    assert (root / "user-notes" / "note.md").read_text(encoding="utf-8") == "mine\n"
    assert (root / "invariants/INV-014.md").exists() is False
    assert {outcome.destination_relative_path for outcome in report.outcomes} == {
        "invariants/INV-014.md"
    }


def test_an_unreadable_manifest_refuses_rather_than_treating_the_destination_as_unowned(
    tmp_path: Path,
) -> None:
    """Requirement 5.1 is only meaningful if the authority's unavailability is loud."""

    root = tmp_path / "vault"
    root.mkdir()
    (root / PROJECTION_MANIFEST_NAME).write_text("{not json", encoding="utf-8")
    report = ManagedProjectionWriter().write(
        ProjectionPlan(
            destination=_profile(root),
            outputs=(_output("invariants/INV-014.md", "INV-014", "# A\n"),),
        )
    )
    assert report.state == "refused"
    assert report.refusal is not None
    assert report.refusal.code == "manifest_unreadable"
    assert not (root / "invariants").exists()


def test_the_manifest_refuses_two_owners_for_one_path() -> None:
    """A duplicate path could not answer "does the substrate own this file"."""

    entry = ManagedOutput(
        destination_relative_path="a.md",
        stable_identity="A",
        record_kind="invariant_revision",
        format="markdown",
        source_snapshot="a" * 64,
        renderer_version="knowledge-view-renderer/1",
        digest="b" * 64,
        byte_count=3,
    )
    with pytest.raises(ValueError):
        ProjectionManifest(generation=1, renderer_version="v", outputs=(entry, entry))


def _tree_snapshot(root: Path) -> dict[str, str]:
    """Every file under one destination with its bytes, for an exact prior-state comparison."""

    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8", errors="ignore")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
