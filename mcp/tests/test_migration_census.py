"""The truth-coverage census and staged migration: the record kinds, the accounting and the boundary.

Every case here protects a distinct operation or a distinct consequential failure, and the failures are
the ones the requirement packet names as its reasons to exist: an inventory that cannot see a surface
with no onboarding, an importer that interprets, a reference repaired by resemblance, a mismatch
reported as a verdict, a denominator taken from the corpus being measured, an ``N`` whose cells do not
close, and a cutover that executes itself. A case that only restated another case's property would be a
variant rather than a protection, so there is one case per property.
"""

from __future__ import annotations

import ast
from pathlib import Path

import apsw
import pytest
from agents_remember.memory.knowledge import census_records
from agents_remember.memory.knowledge.schema_generations import (
    CURRENT_GENERATION,
    GENERATION_8,
    GENERATION_9,
)
from agents_remember.memory.migration import census, census_measures, cutover, inventory, mappings
from agents_remember.memory.migration.baseline import FrozenBaseline, require_frozen_baseline
from agents_remember.memory.migration.parse import (
    CITATION_MARK,
    declared_formats,
    parse_artifact,
)
from agents_remember.memory.migration.resolution import (
    Reference,
    Resolution,
    artifact_mismatches,
    count_resolutions,
    duplicate_anchor_claims,
    metadata_contradiction,
    mismatches_from_resolutions,
    render_report,
    resolve_reference,
)
from agents_remember.models.knowledge.census import CensusDispositionLink
from migration_census_test_support import (
    ABSENT_SOURCE_CARD,
    ADDRESSED_ROUTE,
    CODE_TREE,
    HISTORICAL_CLAIM_TEXT,
    MEMORY_TREE,
    PARSED_CARD,
    REPEATED_CLAIM_TEXT,
    ROUTE_OVERVIEW,
    InventoryRowSpec,
    census_harness,
    disposition_command_for,
    inventory_row_for,
    link_command_for,
    seed_census,
)

CARD_TEXT = """# mcp/src/agents_remember/example.py

| Field | Value |
| --- | --- |
| repository | agents-remember |
| path | `mcp/src/agents_remember/example.py` |
| doc_type | `file-level-onboarding` |
| lastUpdated | 2026-09-18T00:00:00+00:00 |
| lastVerifiedCommitHash | `aaaa` |
| governingOverview | `overview.md` |

## Purpose

The module that does the example thing.

## Code Commentary

### Invariants And Boundaries

- The store is append-only. See cit:(example.py:12-20).
"""

OVERVIEW_TEXT = """# mcp/ — MCP Package Overview

| Field | Value |
| --- | --- |
| repository | agents-remember |
| sourceRoute | `mcp/` |
| doc_type | `route-local-overview` |
| lastUpdated | 2026-09-18T00:00:00+00:00 |
| lastVerifiedCommitHash | `aaaa` |
| governingOverview | `overview.md` |

## Governing Overview

The route overview.
"""

NO_TABLE_TEXT = """# bootstrap/coverage-plan.md

A generated artifact with no metadata table at all.
"""


def _baseline() -> FrozenBaseline:
    """Return the fixture's frozen baseline through the one factory that validates it."""

    baseline = require_frozen_baseline(CODE_TREE, MEMORY_TREE)
    assert isinstance(baseline, FrozenBaseline)
    return baseline


def _reference(*, reviewer: str | None, truths: int = 4) -> census.ReferenceInventory:
    return census.reference_inventory(
        census.ReferenceInventoryDraft(
            inventory_id="reference/onboarding-truths",
            version="v1",
            author="agent:inventory-author",
            reviewer=reviewer,
            truths=tuple(
                census.ReferenceTruth(
                    truth_id=f"RT-{index}",
                    statement=f"reference truth {index}",
                    source="code-examination",
                    source_reference=f"code:module.py:{index}",
                )
                for index in range(truths)
            ),
        )
    )


# -- the parser: declared formats and the metadata-table boundary ---------------------------------


def test_the_metadata_table_ends_before_the_body_so_a_body_table_is_not_a_front_matter_key() -> (
    None
):
    """Catches the parser swallowing the card's own body tables into the front matter."""

    parsed = parse_artifact(PARSED_CARD, CARD_TEXT)
    assert parsed.outcome == "parsed"
    assert parsed.front_matter is not None
    assert parsed.front_matter.keys_in_order == (
        "repository",
        "path",
        "doc_type",
        "lastUpdated",
        "lastVerifiedCommitHash",
        "governingOverview",
    )
    assert "Invariants And Boundaries" not in parsed.front_matter.fields


def test_an_artifact_with_no_metadata_table_is_unsupported_and_reports_what_was_not_parsed() -> (
    None
):
    """Catches a silent skip: an artifact with no declared form must still be a row with evidence."""

    parsed = parse_artifact("bootstrap/coverage-plan.md", NO_TABLE_TEXT)
    assert parsed.outcome == "unsupported"
    assert parsed.front_matter is None
    assert parsed.unparsed_content and "generated artifact" in parsed.unparsed_content


def test_a_route_overview_declares_its_scope_through_source_route_not_through_a_path() -> None:
    """Catches a parser that only understands file cards and drops every route overview."""

    parsed = parse_artifact(ROUTE_OVERVIEW, OVERVIEW_TEXT)
    assert parsed.artifact_kind == "route_local_overview"
    assert parsed.front_matter is not None
    assert "path" not in parsed.front_matter.fields
    assert parsed.front_matter.fields["sourceRoute"] == "mcp/"


def test_every_declared_format_carries_a_reason_and_the_unsupported_form_is_named() -> None:
    """Catches a format list that would silently widen: each entry states why it is supported."""

    formats = declared_formats()
    assert formats
    assert all(entry.reason.strip() for entry in formats)
    parsed = parse_artifact("bootstrap/coverage-plan.md", NO_TABLE_TEXT)
    assert parsed.outcome != "parsed"


def test_citation_keys_are_read_as_references_with_their_location() -> None:
    """Catches a parser that loses the citation keys the reference census counts."""

    parsed = parse_artifact(PARSED_CARD, CARD_TEXT)
    assert parsed.citation_keys == ("example.py:12-20",)
    cited = [
        reference
        for reference in parsed.references
        if reference.reference_text == parsed.citation_keys[0]
    ]
    assert cited, "the citation key the parser read is not among the references it reported"
    assert CITATION_MARK not in cited[0].reference_text
    assert cited[0].field_or_section == "Invariants And Boundaries"


# -- the inventory: the scope, not the corpus -----------------------------------------------------


def test_the_inventory_reports_an_in_scope_source_that_has_no_onboarding_card(
    tmp_path: Path,
) -> None:
    """Catches the corpus-derived inventory: a route with no cards must still produce a row."""

    root = tmp_path / "onboarding"
    (root / "mcp").mkdir(parents=True)
    (root / "mcp" / "carded.py.md").write_text(CARD_TEXT, encoding="utf-8")
    scope = inventory.resolve_scope(
        ["mcp/carded.py", "mcp/test_support/uncarded.py"],
        ["mcp/carded.py.md", "mcp/overview.md"],
        card_path_for_source=lambda path: f"{path}.md",
    )
    report = inventory.build_inventory(baseline=_baseline(), scope=scope, onboarding_root=root)
    absent = {row.source_path for row in report.rows_without_onboarding}
    assert "mcp/test_support/uncarded.py" in absent
    assert report.counts_by_state["absent"] == 1


def test_a_card_whose_declared_source_is_out_of_scope_still_gets_a_row(tmp_path: Path) -> None:
    """Catches the other direction of the same omission: a card pointing at a deleted source."""

    root = tmp_path / "onboarding"
    (root / "mcp").mkdir(parents=True)
    (root / "mcp" / "gone.py.md").write_text(CARD_TEXT, encoding="utf-8")
    report = inventory.build_inventory(
        baseline=_baseline(),
        scope=(inventory.ScopeEntry(source_path="mcp/other.py", route_path=ADDRESSED_ROUTE),),
        onboarding_root=root,
    )
    assert any(row.artifact_path.endswith("gone.py.md") for row in report.rows)
    assert all(row.parsing_outcome != "" for row in report.rows)


def test_an_unreadable_artifact_becomes_a_row_with_its_outcome_rather_than_raising(
    tmp_path: Path,
) -> None:
    """Catches a run that stops on one bad artifact instead of reporting it."""

    root = tmp_path / "onboarding"
    (root / "mcp").mkdir(parents=True)
    (root / "mcp" / "bad.py.md").write_bytes(b"\xff\xfe not utf-8")
    scope = inventory.resolve_scope(
        ["mcp/bad.py"],
        ["mcp/bad.py.md", "mcp/overview.md"],
        card_path_for_source=lambda path: f"{path}.md",
    )
    report = inventory.build_inventory(baseline=_baseline(), scope=scope, onboarding_root=root)
    row = next(item for item in report.rows if item.artifact_path.endswith("bad.py.md"))
    assert row.parsing_outcome == "unreadable"
    assert row.unparsed_content


def test_a_route_path_is_the_normalised_shipped_spelling_with_no_trailing_separator(
    tmp_path: Path,
) -> None:
    """Catches a route spelling the shipped `Route` writer would refuse."""

    root = tmp_path / "onboarding"
    (root / "mcp").mkdir(parents=True)
    scope = inventory.resolve_scope(
        ["mcp/thing.py"],
        ["mcp/thing.py.md"],
        card_path_for_source=lambda path: f"{path}.md",
    )
    assert scope and scope[0].route_path == ADDRESSED_ROUTE


def test_the_declared_doc_type_is_the_authority_for_an_artifacts_kind(tmp_path: Path) -> None:
    """Catches an artifact kind derived from a filename where the artifact declares its own form."""

    root = tmp_path / "onboarding"
    (root / "mcp").mkdir(parents=True)
    # A card whose declared form disagrees with its filename: the declaration is the authority.
    (root / "mcp" / "overview.md").write_text(CARD_TEXT, encoding="utf-8")
    scope = inventory.resolve_scope(
        ["mcp/thing.py"],
        ["mcp/overview.md"],
        card_path_for_source=lambda path: f"{path}.md",
    )
    report = inventory.build_inventory(baseline=_baseline(), scope=scope, onboarding_root=root)
    row = next(item for item in report.rows if item.artifact_path == "mcp/overview.md")
    assert row.observed_doc_type == "file-level-onboarding"
    assert row.artifact_kind == "file_level_onboarding"


# -- the baseline ---------------------------------------------------------------------------------


def test_an_unfrozen_baseline_is_refused_rather_than_accepted_as_a_ref_name() -> None:
    """Catches a baseline read off HEAD, which is a baseline that moves when somebody commits."""

    refused = require_frozen_baseline("main", MEMORY_TREE)
    assert not isinstance(refused, FrozenBaseline)
    assert refused.code == "invalid_reference"
    accepted = require_frozen_baseline(CODE_TREE, MEMORY_TREE)
    assert isinstance(accepted, FrozenBaseline)


def test_two_baselines_are_two_observations_and_never_one_cohort() -> None:
    """Catches a census that merges observations read at two different frozen baselines."""

    first = _baseline()
    second = FrozenBaseline(
        code_tree_id="c" * 40,
        memory_tree_id="d" * 40,
        code_revision="other/code",
        memory_revision="other/memory",
    )
    assert first.key() != second.key()


# -- the mappings: data, not inference ------------------------------------------------------------


def test_an_artifact_whose_declared_form_matches_no_entry_is_unmapped_and_not_best_fitted() -> None:
    """Catches a mapping registry that chooses a nearest entry instead of reporting the state."""

    # A format the registry never declared selects nothing, so the unmapped state stays reachable
    # rather than being absorbed by the wildcard entry.
    assert mappings.select_mapping("some-new-format/v1", "file-level-onboarding") is None
    declared_catch_all = mappings.select_mapping(
        "markdown-metadata-table/v1", "file-level-onboarding"
    )
    assert declared_catch_all is not None
    # A declared format with a doc_type no entry names is the wildcard's own case, and the wildcard
    # is what makes "every artifact gets a disposition" reachable at all.
    assert mappings.mapping_identity(None) == mappings.NO_MAPPING_ID


def test_the_disposition_mapping_is_the_registrys_only_wildcard_and_supplies_no_rationale() -> None:
    """Catches a mapping that would fill a field only a curator may author."""

    wildcards = [entry for entry in mappings.MAPPINGS if entry.artifact_doc_type == "*"]
    assert len(wildcards) == 1
    assert "rationale" not in wildcards[0].supplied_fields


def test_no_mapping_supplies_a_claim_kind_applicability_or_assessment() -> None:
    """Catches an importer that would classify prose: the mapping has no field for it."""

    authored = {"claim_kind", "applicability", "assessment_disposition"}
    for entry in mappings.MAPPINGS:
        assert not authored & set(entry.supplied_fields)


# -- the census record kinds ----------------------------------------------------------------------


def test_the_census_records_carry_no_content_address_digest_or_fingerprint_column() -> None:
    """Catches a census record kind that quietly becomes a second identity authority."""

    for table in GENERATION_9.tables[len(GENERATION_8.tables) :]:
        columns = set(GENERATION_9.columns[table])
        assert not columns & {"content_digest", "logical_digest", "fingerprint", "content_address"}


def test_the_census_record_kinds_join_a_registered_generation_that_appends_to_its_predecessor() -> (
    None
):
    """Catches a census table that retyped, reordered or dropped an earlier generation's declaration."""

    assert GENERATION_9.tables[: len(GENERATION_8.tables)] == GENERATION_8.tables
    for table in GENERATION_8.tables:
        assert GENERATION_9.columns[table] == GENERATION_8.columns[table], table
        assert GENERATION_9.primary_keys[table] == GENERATION_8.primary_keys[table], table
    assert CURRENT_GENERATION is GENERATION_9


def test_a_raw_sql_update_of_a_census_row_is_aborted_by_the_schema() -> None:
    """Catches a census observation being rewritten in place by a path that forgot the rule."""

    with census_harness() as harness:
        store = harness.store()
        try:
            row_id = store.connection.execute(
                "INSERT INTO census_inventory_row (repository_id, inventory_row_id, artifact_path, "
                "artifact_kind, outcome, inventory_state, provenance) VALUES (?,?,?,?,?,?,?)",
                (
                    harness.repository.repository_id,
                    "r1",
                    "a.md",
                    "other",
                    "parsed",
                    "present",
                    "{}",
                ),
            )
            assert row_id is not None
            with pytest.raises(apsw.Error):
                store.connection.execute(
                    "UPDATE census_inventory_row SET artifact_path = 'b.md' "
                    "WHERE inventory_row_id = 'r1'"
                )
        finally:
            store.close()


# -- the write path and the accounting ------------------------------------------------------------


def test_the_seed_writes_every_census_record_kind_through_the_shipped_batch_operation() -> None:
    """Catches a census that invents a second write path or a bulk writer beside the batch."""

    with census_harness() as harness:
        seed_census(harness)
        store = harness.store()
        try:
            assert len(census_records.read_inventory_rows(store)) == 4
            assert len(census_records.read_claims(store)) == 5
            assert len(census_records.read_dispositions(store)) == 4
            claims = census_records.read_claims(store)
            assert sum(len(claim.evidence) for claim in claims) == 5
            assert sum(len(claim.realizations) for claim in claims) == 1
        finally:
            store.close()


def test_a_claim_with_no_assessment_is_pending_and_never_counted_as_supported() -> None:
    """Catches the failure the packet exists to prevent: a fabricated T on a large N."""

    with census_harness() as harness:
        seed_census(harness)
        store = harness.store()
        try:
            claims = census_records.read_claims(store)
        finally:
            store.close()
    unassessed = next(claim for claim in claims if claim.payload.claim_text == REPEATED_CLAIM_TEXT)
    assert census_measures.cell_of(unassessed) == "P"


def test_the_accounting_closes_and_the_three_claim_states_are_separated() -> None:
    """Catches an N whose cells do not sum to it, and a report that loses U or P."""

    with census_harness() as harness:
        seed_census(harness)
        store = harness.store()
        try:
            report = census.build_report(store, baseline_key=harness.baseline.key())
        finally:
            store.close()
    separation = report.separation
    assert separation.total == separation.supported + separation.contradicted + (
        separation.unresolved + separation.pending
    )
    assert separation.supported == 1
    assert separation.contradicted == 1
    assert separation.pending == 2
    assert separation.u == 0 if False else separation.unresolved == 0
    assert separation.historical_non_applicable == 1


def test_the_historical_piece_carries_a_disposition_and_stays_out_of_the_cohort() -> None:
    """Catches a census where a non-applicable piece silently vanishes, or inflates N."""

    with census_harness() as harness:
        seed_census(harness)
        store = harness.store()
        try:
            claims = census_records.read_claims(store)
        finally:
            store.close()
    historical = next(
        claim for claim in claims if claim.payload.claim_text == HISTORICAL_CLAIM_TEXT
    )
    assert historical.payload.disposition == "historical"
    assert not census_measures.claim_enters_cohort(historical)


def test_the_unique_and_occurrence_counts_are_both_reported_for_one_repeated_truth() -> None:
    """Catches a report that lets one easy truth repeated across cards inflate coverage."""

    with census_harness() as harness:
        seed_census(harness)
        store = harness.store()
        try:
            report = census.build_report(store, baseline_key=harness.baseline.key())
        finally:
            store.close()
    assert report.unique_occurrence.occurrences == 4
    assert report.unique_occurrence.unique_claims == 3
    assert report.unique_occurrence.repeats


def test_every_slice_axis_is_reported_and_each_slice_carries_the_four_way_separation() -> None:
    """Catches a global-only census, which the requirement refuses even when its number is right."""

    with census_harness() as harness:
        seed_census(harness)
        store = harness.store()
        try:
            report = census.build_report(store, baseline_key=harness.baseline.key())
        finally:
            store.close()
    for axis in ("family", "category", "consequence", "source_route"):
        slices = report.slice_axis(axis)  # type: ignore[arg-type]
        assert slices, axis
        for item in slices:
            assert item.separation.total == (
                item.separation.supported
                + item.separation.contradicted
                + item.separation.unresolved
                + item.separation.pending
            )


def test_the_category_slice_is_keyed_by_the_curators_authored_claim_kind() -> None:
    """Catches slice keys derived from something other than a recorded association."""

    with census_harness() as harness:
        seed_census(harness)
        store = harness.store()
        try:
            report = census.build_report(store, baseline_key=harness.baseline.key())
        finally:
            store.close()
    keys = {item.key for item in report.slice_axis("category")}
    assert "current_behavior" in keys
    assert "realization_attribution" in keys
    assert "unclassified" in keys


def test_the_correctness_measure_is_accompanied_by_its_unresolved_and_unassessed_counts() -> None:
    """Catches a bare T/(T+F) figure, which Doc12:100 refuses to accept alone."""

    with census_harness() as harness:
        seed_census(harness)
        store = harness.store()
        try:
            report = census.build_report(store, baseline_key=harness.baseline.key())
        finally:
            store.close()
    measure = report.measure("correctness_among_resolved_claims")
    assert measure.value == pytest.approx(0.5)
    assert "U=0" in measure.note and "P=2" in measure.note


def test_the_coverage_measures_are_unmeasured_without_an_independently_reviewed_inventory() -> None:
    """Catches a C/K figure computed from the corpus being measured."""

    with census_harness() as harness:
        seed_census(harness)
        store = harness.store()
        try:
            report = census.build_report(store, baseline_key=harness.baseline.key())
        finally:
            store.close()
    assert report.measure("relevant_truth_coverage").state == "not_measurable"
    assert report.measure("relevant_truth_coverage").value is None
    assert report.reference_inventory_reviewer is None


def test_an_inventory_whose_reviewer_is_its_author_is_refused_and_publishes_no_coverage() -> None:
    """Catches a self-reviewed denominator: independent review is the requirement, not a field."""

    self_reviewed = _reference(reviewer="agent:inventory-author")
    assert not census.coverage_is_publishable(self_reviewed)
    with pytest.raises(ValueError):
        self_reviewed.require_independent_reviewer()


def test_the_report_renders_measures_together_and_never_as_one_composite_score() -> None:
    """Catches the composite index the packet refuses: no single number may stand for the census."""

    with census_harness() as harness:
        seed_census(harness)
        store = harness.store()
        try:
            report = census.build_report(store, baseline_key=harness.baseline.key())
        finally:
            store.close()
    rendered = report.render()
    for name in census_measures.MEASURE_NAMES:
        assert name in rendered or report.measure(name).state == "not_applicable"
    assert "not_measurable" in rendered
    assert "score" not in rendered.casefold()
    assert "grade" not in rendered.casefold()
    assert "pass" not in rendered.casefold().split()


def test_re_running_the_same_records_at_the_same_baseline_refuses_rather_than_duplicating() -> None:
    """Catches a re-run that appends duplicates instead of reporting the identity is already stored."""

    with census_harness() as harness:
        seed_census(harness)
        row = inventory_row_for(
            harness,
            InventoryRowSpec(
                artifact_path=PARSED_CARD, outcome="parsed", inventory_state="present"
            ),
        )
        first = harness.apply((row,))
        assert first.state == "changed"
        second = harness.apply((row,))
        assert second.state == "refused"
        assert second.refusal is not None
        assert second.before == second.after


def test_a_resolution_target_state_is_stored_verbatim_and_never_repaired() -> None:
    """Catches a link whose target state was canonicalised into a match it never resolved to."""

    with census_harness() as harness:
        seed_census(harness)
        command = disposition_command_for(harness, artifact_path=PARSED_CARD, kind="imported")
        unresolved = command.model_copy(
            update={
                "links": (
                    CensusDispositionLink(
                        disposition_id=command.record_id,
                        link_kind="corrected_by",
                        target_ref="record:not-stored-at-this-baseline",
                        target_state="unresolved",
                    ),
                )
            }
        )
        result = harness.apply((unresolved,))
        assert result.state == "changed"
        store = harness.store()
        try:
            stored = next(
                item
                for item in census_records.read_dispositions(store)
                if item.record_id == command.record_id
            )
        finally:
            store.close()
    assert stored.links[0].target_state == "unresolved"
    assert stored.links[0].target_ref == "record:not-stored-at-this-baseline"


def test_a_disposition_split_link_records_which_records_a_claim_became() -> None:
    """Catches a migration record that loses the links Doc12:55 makes part of the record."""

    with census_harness() as harness:
        seed_census(harness)
        store = harness.store()
        try:
            claim_id = census_records.read_claims(store)[0].record_id
        finally:
            store.close()
        result = harness.apply((link_command_for(harness, claim_record_id=claim_id),))
        assert result.state == "changed"
        store = harness.store()
        try:
            dispositions = census_records.read_dispositions(store)
        finally:
            store.close()
        linked = [item for item in dispositions if item.links]
        assert linked and linked[0].links[0].link_kind == "split_into"
        assert linked[0].links[0].target_ref == claim_id


# -- reference resolution -------------------------------------------------------------------------


def _reference_for(text: str, kind: str = "source_path") -> Reference:
    return Reference(
        reference_text=text,
        reference_kind=kind,
        artifact_path=PARSED_CARD,
        location="L1",
        baseline=_baseline(),
    )


def test_reference_resolution_reports_resolved_unresolved_and_ambiguous_as_three_states() -> None:
    """Catches a resolution that defaults a dangling reference or repairs it by resemblance."""

    candidates = {"source_path": ("mcp/a.py", "mcp/b.py")}
    one = resolve_reference(_reference_for("mcp/a.py"), candidates)
    none = resolve_reference(_reference_for("mcp/gone.py"), candidates)
    assert one.state == "resolved"
    assert none.state == "unresolved"
    # Resolution is exact spelling equality and nothing else: a differently-spelled but similar name
    # stays unresolved, which is the ban on repairing a reference by resemblance as a test.
    assert resolve_reference(_reference_for("mcp/A.py"), candidates).state == "unresolved"
    assert resolve_reference(_reference_for("mcp/a"), candidates).state == "unresolved"
    ambiguous = resolve_reference(
        _reference_for("mcp/a.py"), {"source_path": ("mcp/a.py", "mcp/a.py")}
    )
    assert ambiguous.state == "ambiguous"
    assert count_resolutions([one, none, ambiguous]).total == 3


def test_a_resolution_whose_candidates_contradict_its_state_is_refused_at_construction() -> None:
    """Catches a resolution carrying a state its own candidate set contradicts."""

    with pytest.raises(ValueError):
        Resolution(
            reference=_reference_for("mcp/a.py"), state="unresolved", candidates=("mcp/a.py",)
        )
    with pytest.raises(ValueError):
        Resolution(reference=_reference_for("mcp/a.py"), state="resolved", candidates=())


def test_the_reference_counts_partition_totals_every_reference() -> None:
    """Catches a report whose three-state counts do not account for every reference read."""

    candidates = {"source_path": ("mcp/a.py",)}
    resolutions = [
        resolve_reference(_reference_for("mcp/a.py"), candidates),
        resolve_reference(_reference_for("mcp/gone.py"), candidates),
    ]
    counts = count_resolutions(resolutions)
    assert counts.total == 2
    assert (counts.resolved, counts.unresolved, counts.ambiguous) == (1, 1, 0)


def test_a_mechanical_mismatch_is_reported_as_a_fact_and_never_classified() -> None:
    """Catches the pipeline choosing one of Doc13:460's four curator dispositions itself."""

    mismatches = artifact_mismatches(
        artifact_path=ABSENT_SOURCE_CARD,
        declared_source_path="mcp/src/agents_remember/deleted.py",
        present_sources=("mcp/src/agents_remember/example.py",),
        baseline=_baseline(),
    )
    assert len(mismatches) == 1
    rendered = mismatches[0].render()
    assert "declared_source_absent" in rendered
    for verdict in (
        "documentation defect",
        "implementation problem",
        "implementation defect",
        "incorrect attribution",
        "unsupported claim",
    ):
        assert verdict not in rendered


def test_a_metadata_contradiction_is_reported_only_when_the_two_declared_paths_disagree() -> None:
    """Catches a contradiction invented from anything but two declared values."""

    assert (
        metadata_contradiction(
            artifact_path=PARSED_CARD,
            declared_path="mcp/a.py",
            actual_path="mcp/a.py",
            baseline=_baseline(),
        )
        is None
    )
    reported = metadata_contradiction(
        artifact_path=PARSED_CARD,
        declared_path="mcp/a.py",
        actual_path="mcp/b.py",
        baseline=_baseline(),
    )
    assert reported is not None
    assert reported.mismatch_kind == "metadata_contradicts_front_matter"


def test_two_records_claiming_one_anchor_are_reported_with_both_claimants() -> None:
    """Catches a duplicate-anchor fact reported as a verdict about which record is right."""

    duplicates = duplicate_anchor_claims({"anchor-1": ("a", "b"), "anchor-2": ("c",)})
    assert duplicates == (("anchor-1", ("a", "b")),)


def test_the_mismatch_report_renders_byte_identically_whatever_order_the_facts_arrived_in() -> None:
    """Catches a report whose text depends on iteration order, which makes two runs incomparable."""

    first = artifact_mismatches(
        artifact_path="mcp/z.md.md",
        declared_source_path="mcp/z.py",
        present_sources=(),
        baseline=_baseline(),
    )
    second = artifact_mismatches(
        artifact_path="mcp/a.md.md",
        declared_source_path="mcp/a.py",
        present_sources=(),
        baseline=_baseline(),
    )
    assert render_report((*first, *second)) == render_report((*second, *first))


def test_an_unresolved_reference_is_reported_as_a_mismatch_and_not_as_a_refusal() -> None:
    """Catches a census that stops on a dangling reference instead of reporting its state."""

    resolutions = [resolve_reference(_reference_for("mcp/gone.py"), {"source_path": ()})]
    reported = mismatches_from_resolutions(resolutions)
    assert len(reported) == 1
    assert reported[0].mismatch_kind == "declared_source_absent"


# -- the cutover: prepared, never executed --------------------------------------------------------


def test_the_cutover_produces_three_artifacts_and_executes_none_of_them() -> None:
    """Catches a leaf that starts a migration/cutover, which is the escalated action itself."""

    artifacts = cutover.cutover_artifacts()
    assert set(artifacts) == {"criteria", "plan", "proposal"}
    proposal = cutover.CUTOVER_PROPOSAL
    assert proposal.executes_cutover is False
    assert proposal.decision_state == "absent"
    assert proposal.decision_reference is None


def test_the_escalation_quotes_the_governing_boundary_and_its_exact_item() -> None:
    """Catches a proposal that does not name the sentence it is escalated under."""

    proposal = cutover.CUTOVER_PROPOSAL
    assert proposal.boundary_reference == "design/storage-design.md:465"
    assert proposal.boundary_item == "starts migration/cutover"


def test_the_criteria_are_not_softened_and_an_unmet_one_is_reported_with_its_measure() -> None:
    """Catches a criteria set narrowed until it passes, which requirement 8.3 refuses."""

    observations = [
        cutover.measure_criterion(
            declared.criterion_id,
            holds=lambda criterion_id=declared.criterion_id: (
                criterion_id != "CRIT-4-assessment-completion"
            ),
            observed="P = 2 claims remain unassessed",
        )
        for declared in cutover.CUTOVER_CRITERIA
    ]
    verdict = cutover.criteria_verdict(observations)
    assert verdict.startswith("cutover is not yet justified")
    assert "CRIT-4-assessment-completion" in verdict
    assert "P = 2 claims remain unassessed" in verdict


def test_an_evaluation_that_omits_a_declared_criterion_is_refused() -> None:
    """Catches a report where an omitted criterion would read as a satisfied one."""

    with pytest.raises(ValueError):
        cutover.evaluate_criteria([])


def test_the_plan_names_the_archival_step_the_point_of_no_return_and_the_rollback_story() -> None:
    """Catches a plan that would leave the corpus with a live fallback after cutover."""

    plan = cutover.CUTOVER_PLAN
    assert plan.steps and plan.point_of_no_return in {step.order for step in plan.steps}
    assert "not** on any reader" in plan.archival or "not on any reader" in plan.archival
    assert "reversib" in plan.rollback_story
    reversible = [step for step in plan.steps if step.reversible]
    assert reversible


def test_the_plan_creates_no_trigger_no_flag_and_no_scheduled_activation() -> None:
    """Catches a cutover artifact that becomes a gate, which requirement 8.4 forbids."""

    for step in cutover.CUTOVER_PLAN.steps:
        assert "trigger" not in step.action.casefold()
        assert "schedule" not in step.action.casefold()
        assert "flag" not in step.action.casefold()
    assert not hasattr(cutover.CUTOVER_PROPOSAL, "approved")
    assert not hasattr(cutover.CUTOVER_PROPOSAL, "activation")


def test_a_criterion_that_was_never_declared_cannot_be_measured() -> None:
    """Catches a criterion invented at evaluation time rather than reviewed with the others."""

    with pytest.raises(ValueError):
        cutover.measure_criterion("CRIT-99-invented", holds=lambda: True, observed="n/a")


# -- the corpus is the object of measurement, not a repair surface ---------------------------------


def _writing_call_sites(module: Path) -> list[str]:
    """Return every call site in one module that would open the corpus for writing.

    The sweep is over the module's own syntax tree rather than over its text, so a mention of a
    writing method inside a docstring is not mistaken for a write and a write inside a comprehension
    is not missed.
    """

    offenders: list[str] = []
    tree = ast.parse(module.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {"write_text", "write_bytes", "unlink"}:
            offenders.append(f"{module.name}:{node.lineno}:{node.attr}")
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "open" or len(node.args) < 2:
            continue
        mode = node.args[1]
        if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
            offenders.append(f"{module.name}:{node.lineno}:open({mode.value!r})")
    return offenders


def test_the_census_declares_no_route_inference_and_no_corpus_write() -> None:
    """Catches a census that rewrites the corpus in place, which makes a re-run unmeasurable."""

    migration_root = Path(census.__file__).parent
    offenders = [
        site
        for module in sorted(migration_root.glob("*.py"))
        for site in _writing_call_sites(module)
    ]
    assert offenders == []
