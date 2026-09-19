"""``KS-R20@v1`` §1, §2, §3, §5 and §6 as behaviour: the views, the classification, the bounding,
the vault safety, and the mounted surface's own refusals.

Every case names the failure it catches, and the four that carry the leaf are the ordering
differential, the classification completeness scan, the authored-versus-mechanical separation, and
the vault-safety scenario in which two of the checkpoints protect files the substrate never wrote.

Most cases run without a database. The view vocabulary, the closed rule registry, the ordering pass
and the projection writer are all pure functions of their inputs -- which is exactly why the
classification rule can be tested without a store, and why a renderer that opened one would not
typecheck against the port in the first place. The §1 literal case is pure too. The §6 cases are
not: they drive the **registered** ``knowledge_*`` handlers through a real ``FastMCP`` server, so
they open a dataset the build creates, and their point is precisely that the mounted surface
executes rather than merely appearing in the roster.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

import pytest
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.mcp.registration.knowledge import register_knowledge_tools
from agents_remember.memory.knowledge.connection import create_or_validate_schema, open_database
from agents_remember.memory.knowledge.managed_projection import (
    ManagedProjectionWriter,
    ProjectionHooks,
)
from agents_remember.memory.knowledge.schema_generations import GENERATIONS, generation_for_key
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
    VIEW_NAMES,
    VIEW_PAYLOADS,
    VIEW_PURPOSES,
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
from agents_remember.models.tools.knowledge_responses import KnowledgeIntegrityCheckResponse
from mcp.server.fastmcp import FastMCP

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


def test_a_classification_needs_its_evidence_and_a_rule_the_registry_holds() -> None:
    """Two directions of one rule: a class with no evidence is refused, and so is a foreign rule.

    Requirement 2.2 with its own contrapositive, merged because both measure whether a
    classification can exist without the input it is defined by: an unregistered rule cannot produce
    one, and a class whose evidence is missing cannot be emitted as classified either.
    """

    with pytest.raises(ValueError):
        Provenance(provenance_class=AUTHORED_CLASS)
    with pytest.raises(ValueError):
        Provenance(provenance_class=MECHANICAL_CLASS)

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


def test_incompleteness_and_its_continuation_must_agree() -> None:
    """Requirement 3.3 in both directions: rows remaining need a token, and a token needs them.

    One statement about one pair of fields, so one case: a bounded response never presents its first
    page as the whole scope, and a page that says it is complete never carries the token that says it
    is not. Either half alone would let a reader size the scope from the wrong field.
    """

    remaining = view_counts(
        registered_realizations=2, registered_families=1, rows_returned=2, rows_remaining=3
    )
    with pytest.raises(ValueError):
        SourceContextView(
            snapshot=SNAPSHOT,
            counts=remaining,
            completeness=_completeness(),
            renderer_version="knowledge-view-renderer/1",
        )

    complete = view_counts(
        registered_realizations=2, registered_families=1, rows_returned=2, rows_remaining=0
    )
    with pytest.raises(ValueError):
        SourceContextView(
            snapshot=SNAPSHOT,
            counts=complete,
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
    # A report carries its manifest only when it wrote something, and this case is about what it
    # wrote, so the manifest is required rather than read off an optional field.
    assert report.manifest is not None
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


# ---------------------------------------------------------------------------
# §1.1's closed set and order, as a literal.
# ---------------------------------------------------------------------------


def test_the_five_view_names_are_the_closed_ordered_set() -> None:
    """Requirement 1.1: the five names, spelled the same and in the same order.

    Both view-looping cases in this module -- and the differential beside them -- iterate
    ``VIEW_NAMES``, so every one of them stays green under a rename, an insertion, a reorder or a
    sixth member: they check that each view *behaves*, never that the set is the one the packet
    enumerated. This case is the one assertion that cannot be satisfied by iterating the constant it
    is checking, because the expected tuple is written out here rather than derived. It goes red on
    a sixth view, on a rename, on a reorder, and on a name that is admitted by the ``ViewName``
    literal union but missing from the published ``VIEW_PURPOSES`` map -- and the last of those is
    the one a reader of ``VIEW_NAMES`` alone cannot see.
    """

    assert VIEW_NAMES == (
        "source_context",
        "invariant",
        "family",
        "review_matrix",
        "curation_queue",
    )
    assert tuple(VIEW_PURPOSES) == VIEW_NAMES
    # Each payload class declares the view it is a payload *of*, in this order, so a swapped pair of
    # payload types is red here even though the set of five would be unchanged.
    assert tuple(payload.model_fields["view"].default for payload in VIEW_PAYLOADS) == VIEW_NAMES


# ---------------------------------------------------------------------------
# §6: the mounted surface's own refusals, one case per operation family.
# ---------------------------------------------------------------------------

# The namespace every §6 case addresses. It is the fixture's own repository id, so a case that
# reaches a real dataset is reading the namespace the dataset is bound to rather than a stray one.
REPOSITORY_ID = "18710698-4317-43ad-a1d5-e8ab551cc3a7"
# A Git object id for the source-resolution pair the read context requires in full. Its value is
# never resolved by the cases that name it: the refusals they assert are decided before any anchor
# is observed, which is the property "refused before a page is built" states.
CODE_TREE_ID = "0" * 40


@pytest.fixture
def anyio_backend() -> str:
    """Run the §6 cases on asyncio, the backend every registered handler is served on."""

    return "asyncio"


class _RegistrationConfig:
    """The one registration-time fact the knowledge family closes over.

    ``register_knowledge_tools`` reads ``config.workspace_root`` and nothing else, so the stub is a
    permissive chain rather than a built runtime: these cases are about the surface's refusals, not
    about assembling a config.
    """

    workspace_root = Path("/tmp/ar-w5-registration-root")

    def __getattr__(self, name: str) -> _RegistrationConfig:
        return _RegistrationConfig()


def _knowledge_tool_server() -> FastMCP:
    """One server carrying **only** the knowledge family, as its own registrar mounts it."""

    server = FastMCP("knowledge-refusal-probe")
    register_knowledge_tools(server, cast(McpRuntimeConfig, _RegistrationConfig()))
    return server


def _current_generation_dataset(tmp_path: Path, name: str = "knowledge.db") -> Path:
    """One dataset this build creates, bound to the namespace these cases address.

    Written against the production creation path rather than a shared generation helper, because
    these §6 cases need a dataset that is *current* and nothing generation-specific: every refusal
    they assert is decided from the caller's own input, and the one case that reaches a report path
    needs a store that opens rather than a store of some named older generation.
    """

    path = tmp_path / name
    connection = open_database(path)
    try:
        create_or_validate_schema(connection)
        connection.execute(
            "INSERT INTO repository (repository_id, authority_home) VALUES (?, ?)",
            (REPOSITORY_ID, f"memory:{REPOSITORY_ID}"),
        )
    finally:
        connection.close()
    return path


def _unregistered_generation_name() -> str:
    """Return a schema name **no** registered generation carries, derived from the registry."""

    candidate = GENERATIONS[-1].user_version + 1
    while generation_for_key(f"ar-knowledge-sqlite/v{candidate}", candidate) is not None:
        candidate += 1
    return f"ar-knowledge-sqlite/v{candidate}"


async def _call(server: FastMCP, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """The JSON body the named registered tool returned for one call."""

    _content, structured = await server.call_tool(tool, arguments)
    return cast(dict[str, Any], structured)


def _diff_body(schema_name: str, digest: str) -> dict[str, Any]:
    """One comparison body, with both sides' contexts spelled in full.

    The namespace and the snapshot live on each *side* rather than on the request, which is why the
    handler must not add a request-level ``repository_id``: the model declares none and forbids
    undeclared fields, so an added one makes every call of this tool fail before it compares.
    """

    snapshot = {
        "repository_id": REPOSITORY_ID,
        "schema_version": schema_name,
        "logical_digest": digest,
    }
    context = {"repository_id": REPOSITORY_ID, "knowledge": snapshot}
    return {
        "selector": {"kind": "path", "path": "src/subject.py"},
        "before": {"context": context},
        "after": {"context": context},
    }


@pytest.mark.anyio
async def test_the_read_family_refuses_an_ordering_input_it_does_not_admit(
    tmp_path: Path,
) -> None:
    """§2.5 and §6.5: a fifth ordering is refused by name, with no fallback order and no page.

    ``ViewRequest.ordering_input`` is a ``Literal``, so an unadmitted spelling cannot travel through
    a request model -- constructing one raises instead of refusing. The surface therefore asks the
    view module's own closure check before it opens the dataset, and this case is what catches a
    regression to the raise: the caller must receive the refusal the requirement promises, naming
    the offending input, rather than a tool error. A handler that fell back to the declared stable
    ordering would serve a page here and be this leaf's own semantic choice wearing a caller's
    request.
    """

    body = await _call(
        _knowledge_tool_server(),
        "knowledge_read",
        {
            "databasePath": str(_current_generation_dataset(tmp_path)),
            "repositoryId": REPOSITORY_ID,
            "view": "invariant",
            "orderingInput": "symbol_name",
        },
    )

    assert body["state"] == "refused", body
    assert body["refusalCode"] == "unadmitted_ordering_input", body
    assert body["view"] == "invariant", body
    assert "none of the four admitted ordering inputs" in body["refusalDetail"], body


@pytest.mark.anyio
async def test_the_read_family_refuses_a_sixth_view_before_it_touches_a_dataset(
    tmp_path: Path,
) -> None:
    """§1.1, §6.1 and M1-3: the closed set of five, and a dataset that cannot be opened at all.

    The database path in the first half does not exist, which is the second half of that assertion:
    a sixth view is refused from the name alone, so a caller cannot reach a dataset -- or a
    filesystem error -- through a view this surface does not admit. A reader that opened first and
    checked the name later would fail on the missing file instead, which is the mutation the first
    half catches.

    The last three assertions are the family's own promise measured on the inputs an ordinary caller
    actually supplies by mistake. ``registration/knowledge.py``'s docstring says each handler
    "validates its wire request, delegates, and returns the typed shape the response model
    declares", and ``read_knowledge_scope`` already refuses an absent selection as
    ``selected_input_unavailable``. Before this half existed, the same three inputs reached a real
    FastMCP ``server.call_tool`` as ``ToolError``: ``unable to open database file`` for an absent
    path, ``file is not a database`` for a file that is not one, and a raw ``ValidationError`` for a
    diff body that does not validate. A handler that lets any of them escape reddens here, and so
    does one that answers "no rows" for a dataset nobody opened.
    """

    body = await _call(
        _knowledge_tool_server(),
        "knowledge_read",
        {
            "databasePath": "/nonexistent/knowledge-dataset.db",
            "repositoryId": REPOSITORY_ID,
            "view": "sixth_view",
        },
    )

    assert body["state"] == "refused", body
    assert body["refusalCode"] == "unknown_view", body
    assert body["view"] == "sixth_view", body
    assert "sixth_view" in body["refusalDetail"], body

    absent = tmp_path / "absent" / "knowledge-dataset.db"
    for view in ("source_context", "review_matrix"):
        absent_body = await _call(
            _knowledge_tool_server(),
            "knowledge_read",
            {
                "databasePath": str(absent),
                "repositoryId": REPOSITORY_ID,
                "view": view,
            },
        )
        assert absent_body["state"] == "refused", absent_body
        assert absent_body["refusalCode"] == "selected_input_unavailable", absent_body
        assert str(absent) in absent_body["refusalDetail"], absent_body

    not_a_database = tmp_path / "not-a-database.db"
    not_a_database.write_text("this file is not a SQLite database\n", encoding="utf-8")
    unreadable = await _call(
        _knowledge_tool_server(),
        "knowledge_read",
        {
            "databasePath": str(not_a_database),
            "repositoryId": REPOSITORY_ID,
            "view": "source_context",
        },
    )
    assert unreadable["state"] == "refused", unreadable
    assert unreadable["refusalCode"] == "snapshot_unavailable", unreadable
    assert str(not_a_database) in unreadable["refusalDetail"], unreadable

    # The report operation reads a dataset too, so it earns the same typed refusal rather than the
    # traceback an unopened file used to produce.
    report = await _call(
        _knowledge_tool_server(),
        "knowledge_integrity_check",
        {"databasePath": str(absent), "repositoryId": REPOSITORY_ID},
    )
    assert report["state"] == "refused", report
    assert report["refusalCode"] == "selected_input_unavailable", report

    # And the comparison family: a body the shipped request model refuses is a caller error this
    # surface can name, not a transport-level failure.
    invalid = await _call(
        _knowledge_tool_server(),
        "knowledge_diff",
        {
            "databasePath": str(absent),
            "repositoryId": REPOSITORY_ID,
            "beforePath": str(absent),
            "afterPath": str(absent),
            "request": {},
        },
    )
    assert invalid["state"] == "refused", invalid
    assert invalid["refusalCode"] == "invalid_payload", invalid
    assert "KnowledgeDiffRequest" in invalid["refusalDetail"], invalid


@pytest.mark.anyio
async def test_the_read_family_refuses_a_continuation_minted_for_another_walk(
    tmp_path: Path,
) -> None:
    """§3 and §6.4: a continuation bound elsewhere is refused by name, and no page is mixed.

    The token is minted by the view module's own ``continuation_for`` -- it is the real encoding,
    not a forged string -- and then presented to a *different* view's walk. The binding travels
    inside the token, so the surface rebuilds it and refuses rather than handing a position in one
    selection to another selection's page. This is the mismatched-snapshot-continuation input the
    packet's verification evidence names, reaching a mounted handler rather than only the payload
    builder.
    """

    token = continuation_for(
        view="family",
        snapshot=KnowledgeReadSnapshot(
            repository_id=REPOSITORY_ID,
            schema_version="ar-knowledge-sqlite/v9",
            logical_digest="a" * 64,
            context_digest="b" * 64,
        ),
        position=3,
    ).token
    body = await _call(
        _knowledge_tool_server(),
        "knowledge_read",
        {
            "databasePath": str(_current_generation_dataset(tmp_path)),
            "repositoryId": REPOSITORY_ID,
            "view": "invariant",
            "continuation": token,
            "repositoryRoot": str(tmp_path),
            "codeTreeId": CODE_TREE_ID,
        },
    )

    assert body["state"] == "refused", body
    assert body["refusalCode"] == "continuation_unreadable", body
    assert body["view"] == "invariant", body
    assert "minted for another view's walk" in body["refusalDetail"], body


@pytest.mark.anyio
async def test_the_change_family_refuses_a_record_kind_it_has_no_admitted_write_for() -> None:
    """§6.6: the change operation records through admitted owners and authors no second path.

    A census claim is a real record kind of this substrate. It is refused here because the mounted
    change surface has no admitted write operation for it, and the refusal names the kind rather
    than mapping it onto a write this leaf would have to invent. A handler that grew a fallback
    write path, or that accepted the kind and reported success, reddens on the state and the code.
    """

    body = await _call(
        _knowledge_tool_server(),
        "knowledge_change",
        {
            "databasePath": "/nonexistent/knowledge-dataset.db",
            "repositoryId": REPOSITORY_ID,
            "recordKind": "census_claim",
        },
    )

    assert body["state"] == "refused", body
    assert body["refusalCode"] == "registration_absent", body
    assert body["recordKind"] == "census_claim", body
    assert "census_claim" in body["refusalDetail"], body


@pytest.mark.anyio
async def test_the_diff_family_refuses_an_absent_side_by_naming_the_path_it_could_not_read(
    tmp_path: Path,
) -> None:
    """§6.2 and §6.4: a comparison whose side is absent is refused, and no HEAD is substituted.

    The request is a complete, valid comparison body -- which is the whole point, because this is
    the case that catches a surface that cannot get past its own request: injecting a request-level
    ``repository_id`` into a model that forbids undeclared fields made every ``knowledge_diff`` call
    raise a validation error instead of comparing anything, and a roster row cannot see that. The
    before side names a path that does not exist, so the comparison must refuse by naming it rather
    than reaching for a working tree or answering with an empty comparison.
    """

    absent = tmp_path / "absent-before.sqlite"
    body = await _call(
        _knowledge_tool_server(),
        "knowledge_diff",
        {
            "databasePath": str(_current_generation_dataset(tmp_path)),
            "repositoryId": REPOSITORY_ID,
            "beforePath": str(absent),
            "afterPath": str(tmp_path / "absent-after.sqlite"),
            "request": _diff_body("ar-knowledge-sqlite/v9", "a" * 64),
        },
    )

    assert absent.exists() is False, "the case must not create the side it reports as absent"
    assert body["state"] == "refused", body
    assert body["refusalCode"] == "selected_input_unavailable", body
    assert str(absent) in body["refusalDetail"], body
    assert body["repositoryId"] == REPOSITORY_ID, body


@pytest.mark.anyio
async def test_the_integrity_family_reports_the_absence_of_a_detection_run_without_a_verdict(
    tmp_path: Path,
) -> None:
    """§6.7: the report operation reports its limits and produces no compatibility verdict.

    The dataset is real and current, so the operation reaches its own report path rather than
    failing to open anything, and the case pins the two facts that make "no verdict" measurable: a
    namespace with no recorded detection run is reported as *unresolved* with a stated limitation
    rather than as zero conditions, and ``compatible`` carries no verdict. The traversal scope a
    caller named is echoed in the report rather than defaulted away.

    **Where "no verdict" is measured, corrected by ``260918-TSIP-L10``.** This case used to assert
    ``body["compatible"] is None`` -- "present and null, not omitted". That is true of the *model*
    and false of the *wire*: the five builders now route through ``_tool_payload`` like every other
    adapter, and the shared strict envelope dumps with ``exclude_none=True``
    (``models/base.py::ResponseModel.to_payload``), so a declared-and-``None`` field is ABSENT from
    the payload a caller receives. The two assertions below measure the property at the level where
    each half of it lives -- the declaration on the model, the absence of a verdict on the wire --
    and together they are the protection the single old assertion gave: ``compatible`` cannot leave
    the model (first assertion), and a producer that emitted ``true``/``false`` would put the key
    back and fail the second. The registered tool description already states the wire answer
    ("``compatible`` is absent by design, not omitted by accident"); the model's own docstring still
    says "present and null" and is registered as a documentation defect rather than edited here.
    """

    body = await _call(
        _knowledge_tool_server(),
        "knowledge_integrity_check",
        {
            "databasePath": str(_current_generation_dataset(tmp_path)),
            "repositoryId": REPOSITORY_ID,
            "scopeId": "registered-scope-from-the-case",
        },
    )

    assert body["state"] == "reported", body
    assert "compatible" in KnowledgeIntegrityCheckResponse.model_fields, (
        "the no-verdict field left the response model"
    )
    assert "compatible" not in body, (
        f"a compatibility verdict reached the wire: {body.get('compatible')!r}"
    )
    assert body["conditions"] == [], body
    assert body["unresolved"] == ["no recorded detection run to report conditions from"], body
    assert body["limitations"] == [
        "no detection run is recorded for this namespace at this snapshot"
    ], body
    assert body["traversalScope"] == "registered-scope-from-the-case", body


@pytest.mark.anyio
async def test_the_project_family_refuses_an_unresolved_projection_input(
    tmp_path: Path,
) -> None:
    """§5 and §6.8: no view named is a refusal, not an empty projection that "just cleans up".

    The destination is a real, empty directory, so nothing but the missing input can refuse this
    call: a handler that projected zero artifacts and reported success would create a managed
    destination out of a caller's mistake. The refusal names the destination it did not write to and
    the input it could not resolve, and the destination stays uncreated.
    """

    destination = tmp_path / "vault"
    body = await _call(
        _knowledge_tool_server(),
        "knowledge_project",
        {
            "databasePath": str(_current_generation_dataset(tmp_path)),
            "repositoryId": REPOSITORY_ID,
            "destinationRoot": str(destination),
        },
    )

    assert body["state"] == "refused", body
    assert body["refusalCode"] == "unresolved_projection_input", body
    assert body["destinationRoot"] == str(destination), body
    assert "no view was named to project" in body["refusalDetail"], body
    assert not destination.exists(), "a refused projection must not create its destination"


@pytest.mark.anyio
async def test_the_mounted_families_are_exactly_the_five_these_cases_drive() -> None:
    """§6.1: the family module mounts five, this module drives five, and the two sets are equal.

    The roster case in ``test_tools.py`` proves the five names are advertised and that existing names
    keep their positions. It cannot prove that any of them executes, which is finding ``A-2``: the
    family shipped with a roster row and no functional case. This case closes the loop from the
    other end. It reads *this module's own source* for a call naming each mounted tool, so a family
    added to the surface -- or one of these five renamed out from under the cases that drive it --
    reddens here instead of shipping with a roster row and no caller.
    """

    server = _knowledge_tool_server()
    mounted = tuple(tool.name for tool in await server.list_tools())

    assert mounted == (
        "knowledge_read",
        "knowledge_change",
        "knowledge_diff",
        "knowledge_integrity_check",
        "knowledge_project",
    )
    source = Path(__file__).read_text(encoding="utf-8")
    undriven = [name for name in mounted if f'"{name}"' not in source]
    assert not undriven, (
        f"the mounted families {undriven} have no case in this module that calls them by name; a "
        "roster row is not coverage"
    )
