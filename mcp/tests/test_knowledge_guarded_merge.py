"""Focused behaviour of the guarded common-base merge, in five cases.

The unit population is at its declared ceiling, so this module carries the merge's whole contract in
five cases instead of one per scenario. Each case is a named node that a specific guard is
mutation-checked against, and each case asserts the *whole* outcome -- the state, the identity, the
refusal code, and that every input is byte-identical to what it was -- rather than one field of it.

What the cases are, and what each fails on:

* :func:`test_every_structural_violation_is_refused_before_a_session_exists` -- compares each input
  against the declared manifest. Six distinct structural differences (the set of canonical tables,
  an added column, a weakened NOT NULL, a wrong foreign key, a dropped trigger, changed table
  options), plus a reorder and a rename, a weakened trigger body, a changed ``user_version``, and an
  absent or unreadable input. Fails if any comparison stops refusing.
* :func:`test_an_incomplete_delta_applies_silently_and_is_caught_by_coverage_and_replay` -- the
  silent-omission class. A delta built over a subset of the canonical tables is accepted by SQLite,
  and the coverage comparison and the replay each refuse it. Fails if either comparison stops
  measuring, or if the not-supplied marker is conflated with a stored ``SQL NULL``.
* :func:`test_disjoint_edits_from_both_sides_survive_in_a_closed_published_candidate` -- the
  conforming case, including the coverage record, the destination state and the absence of any
  verdict field. Fails if either side's work is dropped, if the published file is not closed, or if a
  publication that changed nothing is reported as a change.
* :func:`test_both_conflicting_edits_refuse_whole_and_preserve_every_input` -- the refusal taxonomy:
  a same-field conflict, two independent insertions of one identity, and a removed row the other
  side's new reference depended on in *both* orientations. Fails if a conflict is applied, resolved,
  or mapped to the wrong code, or if a refusal modifies an input.
* :func:`test_every_base_and_every_input_defect_refuses_without_moving_a_dataset` -- base
  resolution and input integrity: the
  ancestry-proven and supplied claims, a history with no common base, a criss-cross history with two,
  a relative repository root, an input that moved after admission, one file named as two roles, a
  side that rewrote a sealed revision, and an input moved after resolution. Fails if a base is chosen
  rather than proven, or if an input defect is carried into a merge.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import apsw
import pytest
from agents_remember.application.knowledge_merge import (
    merge_resolved_knowledge_datasets,
    resolve_knowledge_merge_base,
)
from agents_remember.kernel.git_command import GitRunnerOptions, run_git
from agents_remember.memory.knowledge import schema
from agents_remember.memory.knowledge.connection import journal_mode, open_read_only_database
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.merge import _coverage_refusal, require_distinct_roles
from agents_remember.memory.knowledge.merge_changeset import (
    NOT_SUPPLIED,
    Delta,
    apply_changeset,
    build_delta,
    replay_delta,
    require_session_capability,
)
from agents_remember.memory.knowledge.merge_schema import (
    declared_structure,
    read_database_structure,
    require_supported_structure,
)
from agents_remember.memory.knowledge.records import encode_authorship
from agents_remember.memory.knowledge.schema_generations import (
    CURRENT_GENERATION,
    generation_of_database,
    registered_version_listing,
)
from agents_remember.memory.knowledge.schema_v2 import APPENDED_TABLES
from agents_remember.models.knowledge.merge import (
    MergeBaseRequest,
    MergeBaseResolution,
    MergeInput,
    MergeRequest,
    ResolvedGitBase,
    SuppliedGitBase,
)
from agents_remember.models.knowledge.result import KnowledgeRefusal
from agents_remember.models.knowledge.snapshot import SnapshotDestinationRequest
from generation_test_support import create_generation_1_store
from merge_case_test_support import (
    BASE_INVARIANT_ID,
    BASE_REVISION_ID,
    GIT_IDENTITY,
    MergeCase,
    add_anchor,
    add_invariant,
    add_realization_claim,
    build_case,
    copy_closed,
    delete_anchor,
    file_digest,
    journal_peer_names,
    labels_of,
    row_counts,
    set_label,
    statements_of,
)

pytestmark = pytest.mark.evidence_unit

OPERATION = "merge_knowledge_datasets"
CLAIM_ID = "88888888-8888-4888-8888-888888888888"
ANCHOR_ID = "44444444-4444-4444-8444-444444444444"
SHARED_REVISION_ID = "77777777-7777-4777-8777-777777777777"
APPENDED_ROUTE_ID = "99999999-9999-4999-8999-999999999999"
APPENDED_ROUTE_PATH = "src/generation-two-scope"
APPENDED_RECORD_ID = "12121212-1212-4212-8212-121212121212"


@pytest.fixture
def case(tmp_path: Path) -> MergeCase:
    return build_case(tmp_path)


# -- 1. the structural preflight and its coverage replay ---------------------------------------


def test_every_structural_violation_is_refused_before_a_session_exists(
    tmp_path: Path, case: MergeCase
) -> None:
    """Every structural difference refuses, and none of them modifies the input it refuses."""

    base_before = file_digest(case.state_path("base"))
    declared = declared_structure(CURRENT_GENERATION)
    assert refusal_for(case.state_path("base")) is None
    assert refusal_for(case.state_path("left"), role="left") is None

    damaged_triggers: frozenset[tuple[str, str, str]] = frozenset()
    for label, edit, expected_table in _structural_violations(base_before, tmp_path):
        damaged = _edited(tmp_path, case, label, edit)
        refusal = refusal_for(damaged)
        assert refusal is not None, label
        assert refusal.operation == OPERATION, label
        assert refusal.table == expected_table, label
        assert refusal.code in {"schema_mismatch", "missing_required_table"}, label
        assert file_digest(case.state_path("base")) == base_before, label
        damaged_structure = read_database_structure(damaged, CURRENT_GENERATION)
        if label == "dropped-trigger":
            damaged_triggers = damaged_structure.declared_tables["invariant_revision"].triggers
            intact = read_database_structure(case.state_path("base"), CURRENT_GENERATION)
            assert damaged_triggers != intact.declared_tables["invariant_revision"].triggers

    reordered = _edited(tmp_path, case, "reordered", _reorder_invariant_columns)
    renamed = _edited(
        tmp_path,
        case,
        "renamed",
        lambda path: _rewrite_schema(
            path,
            table_type="table",
            name="invariant",
            replace=("label_provenance TEXT NOT NULL", "provenance_label TEXT NOT NULL"),
        ),
    )
    for damaged in (reordered, renamed):
        refusal = refusal_for(damaged)
        assert refusal is not None and refusal.table == "invariant"
        assert refusal.expected != refusal.observed

    weakened_trigger = _edited(
        tmp_path,
        case,
        "weakened-trigger",
        lambda path: _rewrite_schema(
            path,
            table_type="trigger",
            name="invariant_no_delete",
            replace=("SELECT RAISE(ABORT, ", "SELECT (0) AND ("),
        ),
    )
    weakened_refusal = refusal_for(weakened_trigger)
    assert weakened_refusal is not None and weakened_refusal.table == "invariant"
    # Re-scoped by `KS-R10` §Shipped Assertions. A base copy mutated to ``PRAGMA user_version = 2``
    # used to be refused with ``(expected, observed) == ("1", "2")``. It is not refused that way any
    # more, and must not be: version 2 is a *supported* generation, so the input resolves to
    # generation 2 and is refused instead by the generation-2 table it does not have. The dataset
    # this is asserted on is a genuine version-1 dataset -- the fixture's own store is created as
    # generation 2 now, so mutating its version to 2 would change nothing.
    version_2_input = tmp_path / "version-2-input.sqlite"
    with create_generation_1_store(version_2_input, case.repository.repository_id):
        pass
    _mutate(version_2_input, ("PRAGMA user_version = 2", ()))
    version_refusal = refusal_for(version_2_input)
    assert version_refusal is not None
    assert version_refusal.code == "missing_required_table"
    # The first generation-2 table the version-1 dataset does not have, in manifest order.
    assert version_refusal.table == APPENDED_TABLES[0]
    assert version_refusal.observed == "absent from the base input"

    # The second case the replacement fact requires: "unsupported generation" stays asserted by
    # something other than a structurally incomplete generation 2.
    unregistered = _edited(
        tmp_path,
        case,
        "unregistered-version",
        lambda path: _mutate(path, ("PRAGMA user_version = 99", ())),
    )
    unregistered_refusal = refusal_for(unregistered)
    assert unregistered_refusal is not None
    assert unregistered_refusal.code == "unsupported_schema"
    assert unregistered_refusal.expected == registered_version_listing()
    assert unregistered_refusal.observed == "99"

    absent = refusal_for(tmp_path / "absent.sqlite")
    junk = tmp_path / "junk.sqlite"
    junk.write_bytes(b"this is not a SQLite database at all")
    unreadable = refusal_for(junk)
    assert absent is not None and absent.code == "schema_mismatch"
    assert "could not be read" in absent.detail
    assert unreadable is not None and unreadable.code == "schema_mismatch"
    assert declared.declared_tables["invariant_revision"].triggers != damaged_triggers


def test_an_incomplete_delta_applies_silently_and_is_caught_by_coverage_and_replay(
    tmp_path: Path,
) -> None:
    """A delta over a subset of the canonical tables is accepted by SQLite and refused by both
    measures of completeness. The named guard for the silent-omission class."""

    case = build_case(tmp_path)
    complete = build_delta(
        case.state_path("right"),
        case.state_path("base"),
        side="right",
        generation=CURRENT_GENERATION,
    )
    partial = _partial_delta(case, "right", ("invariant_revision",))
    target = copy_closed(case.state_path("base"), tmp_path / "partial-target.sqlite")

    applied = apply_changeset(partial, target)

    assert partial.changeset != complete.changeset
    assert applied.conflicted is False and applied.detail == ""
    assert file_digest(target) != file_digest(case.state_path("base"))

    # Coverage is measured over the generation the datasets themselves declare, so the test
    # resolves it the way the production path does rather than assuming the build's generation.
    base_connection = open_read_only_database(case.state_path("base"))
    try:
        generation = generation_of_database(base_connection)
    finally:
        base_connection.close()
    refusal = _coverage_refusal(
        partial,
        {"base": case.state_path("base"), "right": case.state_path("right")},
        generation,
    )

    assert refusal is not None
    assert refusal.code == "changeset_incomplete"
    assert refusal.table == "invariant"

    replayed, replay_refusal = replay_delta(
        _partial_delta(case, "right", ()), tmp_path / "empty-replay.sqlite", OPERATION
    )

    assert replayed is None
    assert replay_refusal is not None and replay_refusal.code == "changeset_incomplete"

    # The complete delta replays onto its own side, and a column the changeset does not supply is
    # a marker rather than a stored SQL NULL.
    base_before, side_before = (
        file_digest(case.state_path("base")),
        file_digest(case.state_path("left")),
    )
    delta = build_delta(
        case.state_path("left"),
        case.state_path("base"),
        side="left",
        generation=CURRENT_GENERATION,
    )
    edited = copy_closed(case.state_path("base"), tmp_path / "labelled.sqlite")
    set_label(edited, "a changed label")
    updates = [
        change
        for change in build_delta(
            edited,
            case.state_path("base"),
            side="left",
            generation=CURRENT_GENERATION,
        ).operations
        if change.operation == "UPDATE"
    ]
    landed, landed_refusal = replay_delta(delta, tmp_path / "replay.sqlite", OPERATION)

    assert landed_refusal is None
    assert landed == case.identity("left").logical_digest
    assert file_digest(case.state_path("base")) == base_before
    assert file_digest(case.state_path("left")) == side_before
    insert = next(change for change in delta.operations if change.operation == "INSERT")
    assert insert.supplied == frozenset(schema.CANONICAL_COLUMNS[insert.table])
    assert updates[0].column_value("display_label", side="new") == "a changed label"
    assert updates[0].column_value("label_provenance", side="new") == NOT_SUPPLIED
    assert "label_provenance" not in updates[0].supplied

    assert require_session_capability(OPERATION) is None
    assert "ENABLE_SESSION" in apsw.compile_options


# -- 2. the conforming merge --------------------------------------------------------------------


def test_disjoint_edits_from_both_sides_survive_in_a_closed_published_candidate(
    case: MergeCase, tmp_path: Path
) -> None:
    """Both sides' own work is retained, the result is a closed file, and it carries no verdict."""

    destination = tmp_path / "memory" / "merged.sqlite"

    outcome = run(case, destination=destination)

    assert outcome.state == "structurally_merged"
    assert outcome.publication_state == "published"
    assert outcome.destination_ref == str(destination)
    assert outcome.merged_identity is not None
    left_statements = statements_of(case.state_path("left"))
    right_statements = statements_of(case.state_path("right"))
    merged_statements = statements_of(destination)
    assert set(left_statements) | set(right_statements) <= set(merged_statements)
    assert len(merged_statements) == len(left_statements) + len(right_statements) - 1
    assert set(labels_of(case.state_path("left"))) | set(labels_of(case.state_path("right"))) <= (
        set(labels_of(destination))
    )
    assert journal_peer_names(destination) == []
    reader = open_read_only_database(destination)
    try:
        assert journal_mode(reader) == "delete"
    finally:
        reader.close()
    assert dataset_identity(destination).logical_digest == outcome.merged_identity.logical_digest
    assert [coverage.side for coverage in outcome.coverage] == ["left", "right"]
    for coverage in outcome.coverage:
        # Coverage spans the selected generation's whole table set, not a fixed count. These
        # datasets are created by this build, so the selected generation is ``CURRENT_GENERATION``
        # -- the ten generation-1 tables, the six generation 2 appends, and whatever the newest
        # leaf appends after them. A bare ``== 10`` encoded the single-generation assumption, and a
        # literal generation number would encode the same mistake one generation later.
        assert len(coverage.tables) == len(CURRENT_GENERATION.tables)
        assert coverage.replayed_digest == coverage.source_digest
        assert coverage.operations == sum(entry.operations for entry in coverage.tables)
    assert set(outcome.changeset_digests) == {"left", "right"}

    published = file_digest(destination)
    retry = run(case, destination=destination, expected_destination=dataset_identity(destination))

    assert retry.state == "structurally_merged"
    assert retry.publication_state == "no_change"
    assert retry.merged_identity == outcome.merged_identity
    assert file_digest(destination) == published

    stale = run(case, destination=destination, expected_destination=None)

    assert stale.state == "refused" and stale.refusal is not None
    assert stale.refusal.code == "destination_stale"
    assert file_digest(destination) == published

    rendered = outcome.model_dump(mode="json")
    forbidden = {
        "compatible",
        "incompatible",
        "semantic_assessment",
        "semanticAssessment",
        "harmless",
        "safe",
        "approved",
        "accepted",
        "strengthens",
        "behavioral_conflict",
        "verdict",
    }
    assert rendered["state"] == "structurally_merged"
    assert not forbidden & set(rendered)


# -- 3. the refusal taxonomy --------------------------------------------------------------------


def _appended_table_shape(case: MergeCase, states: dict[str, Path]) -> None:
    """Author two generation-2 rows into the right side's state before it is committed.

    One ``route`` and one ``knowledge_record``, which the record's own ``governing_route_id``
    references: two of the six tables the running build appends, so the comparison has to resolve
    generation 2 for a table with a foreign key into another appended table as well as for a leaf
    table. No ``record_revision`` row is invented here -- this leaf has no production writer for a
    sealed revision (see the fix report's `L10-R4`), so a hand-written one would exercise this
    case's own digest recipe rather than the merge.
    """

    connection = apsw.Connection(str(states["right"]))
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO route (repository_id, route_id, parent_route_id, path, provenance) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                case.repository.repository_id,
                APPENDED_ROUTE_ID,
                None,
                APPENDED_ROUTE_PATH,
                encode_authorship(case.authorship),
            ),
        )
        connection.execute(
            "INSERT INTO knowledge_record (repository_id, record_id, kind, authority_home, "
            "lifecycle, governing_route_id, record_schema, provenance) VALUES (?, ?, ?, ?, ?, ?, "
            "?, ?)",
            (
                case.repository.repository_id,
                APPENDED_RECORD_ID,
                "finding",
                "agents-remember",
                "draft",
                APPENDED_ROUTE_ID,
                "finding/v1",
                encode_authorship(case.authorship),
            ),
        )
    finally:
        connection.close()


def test_a_right_side_change_to_an_appended_table_merges_instead_of_aborting(
    tmp_path: Path,
) -> None:
    """`L10-R2`: a change to a generation-2 table is compared against the *selected* generation.

    ``route`` is generation 2's own table, so a right-side change to it is a change to a table the
    pinned generation-1 registries do not describe. Before the repair, the structural comparison's
    row helpers read those pinned registries, so this merge aborted with an unhandled ``KeyError``
    where a typed outcome belongs. The case drives the public entry point rather than the helpers,
    because a case that calls ``_row_of`` directly can pass while the operation's own call path is
    wrong -- and because the coverage record, not the row read, is what says the appended table was
    actually replayed.
    """

    appended = build_case(
        tmp_path / "appended-table",
        diverging_revisions=False,
        diverging_identities=False,
        shape=_appended_table_shape,
    )
    destination = tmp_path / "appended-table" / "memory" / "merged.sqlite"

    outcome = run(appended, destination=destination)

    assert outcome.state == "structurally_merged", outcome.refusal
    assert outcome.publication_state == "published"
    right = next(entry for entry in outcome.coverage if entry.side == "right")
    assert len(right.tables) == len(CURRENT_GENERATION.tables)
    assert right.operations >= 1
    reader = open_read_only_database(destination)
    try:
        assert [str(row[0]) for row in reader.execute("SELECT path FROM route")] == [
            APPENDED_ROUTE_PATH
        ]
        assert [
            tuple(str(value) for value in row)
            for row in reader.execute("SELECT record_id, governing_route_id FROM knowledge_record")
        ] == [(APPENDED_RECORD_ID, APPENDED_ROUTE_ID)]
    finally:
        reader.close()


def test_both_conflicting_edits_refuse_whole_and_preserve_every_input(tmp_path: Path) -> None:
    """Each conflicting shape refuses with its own code, publishes nothing and moves no input.

    Every refusal also names the row it refused -- the exact key the engine handed the conflict
    callback -- which is asserted rather than left to the integration module, so this node fails if
    a key is ever read from the wrong side of a change.
    """

    same_field = build_case(
        tmp_path / "same-field",
        diverging_revisions=False,
        diverging_identities=False,
        shape=_same_field_conflict,
    )
    destination = tmp_path / "same-field" / "memory" / "merged.sqlite"

    conflict = run(same_field, destination=destination)

    refusal = assert_refused_without_moving_any_input(same_field, conflict)
    assert refusal.code == "conflicting_values"
    assert refusal.table == "invariant"
    assert refusal.operation == OPERATION
    assert conflict.conflict is not None
    assert conflict.conflict.code == "conflicting_values"
    assert conflict.conflict.attribution == "engine_attributed"
    assert conflict.conflict.operation == "UPDATE"
    # The row the engine refused is the one the base already held, and the record names it exactly:
    # a changeset omits the key columns of an UPDATE, so this is the assertion that a key read from
    # the operation's new side would fail.
    assert conflict.conflict.record_id == (
        f"{same_field.repository.repository_id}/{BASE_INVARIANT_ID}"
    )
    assert not destination.exists()
    assert labels_of(same_field.state_path("left")) == ["the left side's label"]
    assert labels_of(same_field.state_path("right")) == ["the right side's label"]

    duplicate = build_case(
        tmp_path / "duplicate",
        diverging_revisions=False,
        diverging_identities=False,
        shape=_shared_revision_shape,
    )

    duplicate_outcome = run(duplicate)

    duplicate_refusal = assert_refused_without_moving_any_input(duplicate, duplicate_outcome)
    assert duplicate_refusal.code == "duplicate_identity"
    assert duplicate_refusal.table == "invariant_revision"
    assert duplicate_outcome.conflict is not None
    assert duplicate_outcome.conflict.code == "duplicate_identity"
    # An INSERT is the one operation with no old side, so its key is read from the new side.
    assert duplicate_outcome.conflict.record_id == (
        f"{duplicate.repository.repository_id}/{SHARED_REVISION_ID}"
    )

    for removing in ("left", "right"):
        reference = _reference_case(tmp_path / f"reference-{removing}", removing=removing)
        outcome = run(reference)
        reference_refusal = assert_refused_without_moving_any_input(reference, outcome)
        assert reference_refusal.code == "delete_reference_conflict", removing
        assert outcome.conflict is not None
        assert outcome.conflict.code == "delete_reference_conflict"
        assert outcome.conflict.attribution == "engine_reported_without_row"
        assert outcome.conflict.table is None and outcome.conflict.detail is not None

        # Whichever side performed the removal, the result is the same refusal -- and the two
        # orientations are genuinely different inputs, which the counts below establish rather than
        # assert by naming them.
        assert_removal_orientation(reference, removing)


# -- 4. base adjudication and input integrity ---------------------------------------------------


def test_every_base_and_every_input_defect_refuses_without_moving_a_dataset(
    tmp_path: Path,
) -> None:
    """A base is proven or refused -- never chosen -- and no input defect reaches a delta."""

    assert_input_defects_refuse(tmp_path / "defects")
    case = build_case(tmp_path / "proven")
    world = case.world
    assert world is not None
    _commit_on_top(world.root, world.right_commit, "decoy-tip", "decoy.txt")

    proven = resolve(case)

    assert proven.git_base_commit == world.base_commit
    assert proven.uniqueness_checked is True
    assert proven.base_identity == case.identity("base")
    assert proven.left_identity == case.identity("left")
    assert proven.right_identity == case.identity("right")

    supplied = resolve_knowledge_merge_base(
        base_request(case, git_base=SuppliedGitBase(commit_id="a" * 40, tree_id="b" * 40))
    )
    assert isinstance(supplied, MergeBaseResolution)
    assert supplied.git_base_commit is None and supplied.uniqueness_checked is False

    unrelated = _commit_on_top(world.root, world.right_commit, "unrelated", "unrelated.txt")
    no_base = resolve_knowledge_merge_base(
        base_request(
            case,
            git_base=ResolvedGitBase(
                repository_root=world.root,
                base_commit_id=unrelated,
                left_commit_id=unrelated,
                right_commit_id=world.right_commit,
            ),
        )
    )
    assert isinstance(no_base, KnowledgeRefusal)
    assert no_base.code == "common_base_mismatch"
    assert no_base.expected == unrelated and no_base.observed == world.right_commit

    # A base that *is* an ancestor of the left side and not of the right: the side-chain commit
    # named as the base sits on the left's own history, so the uniqueness of the claim is not the
    # question and only the per-side ancestry check can refuse it.
    one_sided_left = _commit_on_top(world.root, world.right_commit, "one-sided", "one-sided.txt")
    one_sided_right = _commit_on_top(world.root, one_sided_left, "sibling", "sibling.txt")
    partial = resolve_knowledge_merge_base(
        base_request(
            case,
            git_base=ResolvedGitBase(
                repository_root=world.root,
                base_commit_id=one_sided_left,
                left_commit_id=world.right_commit,
                right_commit_id=one_sided_right,
            ),
        )
    )
    assert isinstance(partial, KnowledgeRefusal)
    assert partial.code == "common_base_mismatch"
    assert partial.expected == one_sided_left

    first = _commit_on_top(world.root, world.base_commit, "first-branch", "first.txt")
    second = _commit_on_top(world.root, world.base_commit, "second-branch", "second.txt")
    left_merge = _merge_commit(world.root, first, second, "left-merge")
    right_merge = _merge_commit(world.root, second, first, "right-merge")
    assert len(_common_bases(world.root, left_merge, right_merge)) == 2

    ambiguous = resolve_knowledge_merge_base(
        base_request(
            case,
            git_base=ResolvedGitBase(
                repository_root=world.root,
                base_commit_id=first,
                left_commit_id=left_merge,
                right_commit_id=right_merge,
            ),
        )
    )
    assert isinstance(ambiguous, KnowledgeRefusal)
    assert ambiguous.code == "common_base_ambiguous"
    assert ambiguous.observed is not None and "," in ambiguous.observed

    relative = resolve_knowledge_merge_base(
        base_request(
            case,
            git_base=ResolvedGitBase(
                repository_root=Path("relative/scenario"),
                base_commit_id=world.base_commit,
                left_commit_id=world.left_commit,
                right_commit_id=world.right_commit,
            ),
        )
    )
    assert isinstance(relative, KnowledgeRefusal)
    assert relative.code == "common_base_unavailable"


# -- the shared helpers these cases use ---------------------------------------------------------


def assert_input_defects_refuse(root: Path) -> None:
    """Require every input defect in one case to refuse, and no refusal to move a dataset."""

    case = build_case(root)
    before = {role: file_digest(case.state_path(role)) for role in ("base", "left", "right")}
    moved = case.identity("left")
    base_input, left_input, right_input = case.merge_inputs()
    stale = resolve_knowledge_merge_base(
        MergeBaseRequest(
            repository=case.repository,
            git_base=_ancestry_claim(case),
            inputs=(
                base_input,
                MergeInput(
                    role="left",
                    reference="git:left",
                    database_path=case.state_path("right"),
                    expected_identity=moved,
                ),
                right_input,
            ),
        )
    )
    assert isinstance(stale, KnowledgeRefusal)
    assert stale.code == "stale_precondition"
    assert stale.expected == moved.logical_digest
    for role, digest in before.items():
        assert file_digest(case.state_path(role)) == digest

    shared = root / "one.sqlite"
    shared.write_bytes(b"not a database either")
    roles = require_distinct_roles({"base": shared, "left": shared, "right": shared})
    assert roles is not None and roles.code == "selected_input_unavailable"

    absent = resolve_knowledge_merge_base(
        MergeBaseRequest(
            repository=case.repository,
            git_base=SuppliedGitBase(commit_id="a" * 40, tree_id="b" * 40),
            inputs=(
                MergeInput(
                    role="base",
                    reference="git:base",
                    database_path=root / "absent.sqlite",
                    expected_identity=case.identity("base"),
                ),
                left_input,
                right_input,
            ),
        )
    )
    assert isinstance(absent, KnowledgeRefusal)
    assert absent.code == "selected_input_unavailable" and absent.record_id is not None
    # One file named as every role is refused, and a dataset that moved after resolution is refused
    # before any delta is derived.
    self_merge = build_case(root / "self-merge")
    shared_path = self_merge.state_path("base")
    assert self_merge.world is not None
    self_outcome = merge_resolved_knowledge_datasets(
        MergeRequest(
            resolution=resolve(self_merge),
            databases={"base": shared_path, "left": shared_path, "right": shared_path},
        )
    )
    # One file named as all three roles is refused by the distinct-role guard, which runs before
    # the identity comparison precisely so this shape reports what is actually wrong with it.
    self_refusal = assert_refused_without_moving_any_input(self_merge, self_outcome)
    assert self_refusal.code == "selected_input_unavailable"
    assert self_refusal.observed is not None and self_refusal.expected is not None

    drifting = build_case(root / "drifting")
    drifting_resolution = resolve(drifting)
    add_invariant(drifting.state_path("left"), drifting, "an obligation authored after resolution")
    drift_outcome = merge_resolved_knowledge_datasets(
        MergeRequest(resolution=drifting_resolution, databases=drifting.databases_by_role())
    )
    drift_refusal = assert_refused_without_moving_any_input(drifting, drift_outcome)
    assert drift_refusal.code == "stale_precondition"
    assert drift_refusal.observed == dataset_identity(drifting.state_path("left")).logical_digest
    assert drift_refusal.expected == drifting_resolution.left_identity.logical_digest

    # The same shape reached a second time through the *other* side proves the comparison is made
    # for every dataset and not only for the one an earlier assertion happened to name.
    other = build_case(root / "drifting-other")
    other_resolution = resolve(other)
    add_invariant(other.state_path("right"), other, "an obligation authored after resolution")
    other_outcome = merge_resolved_knowledge_datasets(
        MergeRequest(resolution=other_resolution, databases=other.databases_by_role())
    )
    other_refusal = assert_refused_without_moving_any_input(other, other_outcome)
    assert other_refusal.code == "stale_precondition"
    assert other_refusal.observed == dataset_identity(other.state_path("right")).logical_digest


def _ancestry_claim(case: MergeCase) -> ResolvedGitBase:
    world = case.world
    assert world is not None
    return ResolvedGitBase(
        repository_root=world.root,
        base_commit_id=world.base_commit,
        left_commit_id=world.left_commit,
        right_commit_id=world.right_commit,
    )


def base_request(case: MergeCase, *, git_base: object) -> MergeBaseRequest:
    """Build one base-resolution request over this case's three datasets."""

    return MergeBaseRequest(
        repository=case.repository,
        git_base=git_base,  # type: ignore[arg-type]
        inputs=case.merge_inputs(),
    )


def resolve(case: MergeCase, *, ancestry: bool = True) -> MergeBaseResolution:
    """Resolve this case's base, requiring that the resolution succeeded."""

    claim = (
        _ancestry_claim(case) if ancestry else SuppliedGitBase(commit_id="a" * 40, tree_id="b" * 40)
    )
    outcome = resolve_knowledge_merge_base(base_request(case, git_base=claim))
    assert not isinstance(outcome, KnowledgeRefusal), outcome
    return outcome


def run(case: MergeCase, *, destination: Path | None = None, expected_destination=None):
    """Merge this case's datasets, optionally publishing to an admitted destination."""

    return merge_resolved_knowledge_datasets(
        MergeRequest(
            resolution=resolve(case),
            databases=case.databases_by_role(),
            destination=None
            if destination is None
            else SnapshotDestinationRequest(
                destination_path=destination, expected_destination=expected_destination
            ),
        )
    )


def assert_refused_without_moving_any_input(case: MergeCase, outcome) -> KnowledgeRefusal:
    """Require a refused outcome that published nothing and left all three inputs untouched."""

    before = {role: file_digest(case.state_path(role)) for role in ("base", "left", "right")}
    assert outcome.state == "refused"
    assert outcome.merged_identity is None
    assert outcome.refusal is not None
    for role, digest in before.items():
        assert file_digest(case.state_path(role)) == digest
    return outcome.refusal


def assert_removal_orientation(case: MergeCase, removing: str) -> None:
    """Require the named side to be the one that ends without the anchor, and the other to cite it.

    The two orientations are different merges: with the removal on the left side the *reference*
    arrives through the applied delta, and with it on the right side the applied delta carries the
    removal. The counts make that a measurement instead of a restatement of the parameter.
    """

    citing = "left" if removing == "right" else "right"
    removing_counts = row_counts(case.state_path(removing))
    citing_counts = row_counts(case.state_path(citing))

    assert removing_counts["source_anchor"] == 0
    assert citing_counts["source_anchor"] == 1
    assert removing_counts["realization_claim"] == 0
    assert citing_counts["realization_claim"] == 1


def refusal_for(database: Path, *, role: str = "base") -> KnowledgeRefusal | None:
    """Run the preflight for one input, under the generation **that input declares**.

    Re-scoped by `KS-R10` §Shipped Assertions: this helper used to pass
    ``declared=declared_structure()`` -- the *build's* generation -- which is the same defect
    requirement 6.2 removes from ``merge.py`` and ``merge_base.py``. It now passes no generation at
    all, so ``require_supported_structure`` selects the input's own declared generation exactly as
    the production preflight does.
    """

    return require_supported_structure(database, OPERATION, role=role)


def _structural_violations(
    base_before: str, tmp_path: Path
) -> list[tuple[str, Callable[[Path], None], str]]:
    """Six distinct classes of structural difference, each with the table the refusal must name."""

    del base_before, tmp_path
    return [
        (
            "missing-canonical-table",
            lambda path: _mutate(
                path,
                ("DROP TRIGGER realization_claim_no_rewrite", ()),
                ("DROP TABLE realization_claim", ()),
            ),
            "realization_claim",
        ),
        (
            "extra-undeclared-table",
            lambda path: _mutate(path, ("CREATE TABLE unfamiliar (payload TEXT) STRICT", ())),
            "unfamiliar",
        ),
        (
            "added-column",
            lambda path: _mutate(path, ("ALTER TABLE invariant ADD COLUMN extra_note TEXT", ())),
            "invariant",
        ),
        (
            "weakened-not-null",
            lambda path: _rewrite_schema(
                path,
                table_type="table",
                name="invariant",
                replace=("display_label TEXT NOT NULL", "display_label TEXT"),
            ),
            "invariant",
        ),
        (
            "wrong-foreign-key",
            lambda path: _rewrite_schema(
                path,
                table_type="table",
                name="invariant",
                replace=(
                    "REFERENCES repository(repository_id)",
                    "REFERENCES nowhere_at_all(repository_id)",
                ),
            ),
            "invariant",
        ),
        (
            "dropped-trigger",
            lambda path: _mutate(path, ("DROP TRIGGER invariant_revision_no_update", ())),
            "invariant_revision",
        ),
        (
            "changed-table-options",
            lambda path: _rewrite_schema(
                path,
                table_type="table",
                name="source_anchor",
                replace=(") STRICT", ") WITHOUT ROWID, STRICT"),
            ),
            "source_anchor",
        ),
    ]


def _edited(tmp_path: Path, case: MergeCase, label: str, edit: Callable[[Path], None]) -> Path:
    """Return a copy of the base dataset with one explicit structural edit applied."""

    target = tmp_path / f"damaged-{label}.sqlite"
    target.write_bytes(case.state_path("base").read_bytes())
    edit(target)
    return target


def _partial_delta(case: MergeCase, side: str, tables: tuple[str, ...]) -> Delta:
    """Build one delta whose session was told about only a subset of the canonical tables.

    This is the shape a missing ``session.attach`` produces in production: the connection still holds
    every table, the session was simply never told about one of them, and SQLite reports no error.
    The delta's bookkeeping is deliberately left empty, because the point of the case is that nothing
    but a comparison against the datasets can see the omission.
    """

    connection = apsw.Connection(str(case.state_path(side)), flags=apsw.SQLITE_OPEN_READONLY)
    connection.execute("ATTACH DATABASE ? AS ks_base", (str(case.state_path("base")),))
    try:
        session = apsw.Session(connection, "main")
        try:
            for table in tables:
                session.attach(table)
                session.diff("ks_base", table)
            changeset = session.changeset()
        finally:
            session.close()
    finally:
        connection.close()
    return Delta(
        side=side,
        side_path=case.state_path(side),
        base_path=case.state_path("base"),
        changeset=changeset,
        operations=(),
        changed_tables=(),
        operation_counts={table: 0 for table in schema.CANONICAL_TABLES},
    )


def _same_field_conflict(case: MergeCase, states: dict[str, Path]) -> None:
    """Both sides change the same mutable label to different values."""

    del case
    set_label(states["left"], "the left side's label")
    set_label(states["right"], "the right side's label")


def _shared_revision_shape(case: MergeCase, states: dict[str, Path]) -> None:
    """Two sides independently author a revision under one identity, with different payloads."""

    add_invariant(states["left"], case, "a diverging obligation on the left")
    for version, path in (("v2", states["left"]), ("v3", states["right"])):
        _insert_revision(
            path,
            repository_id=case.repository.repository_id,
            revision_id=SHARED_REVISION_ID,
            display_version=version,
            statement="Both sides independently authored a revision under this identity.",
        )


def _tamper(case: MergeCase, states: dict[str, Path]) -> None:
    """Rewrite a sealed revision in place, then restore the trigger that refused the write."""

    add_invariant(states["left"], case, "a diverging obligation on the left")
    path = states["right"]
    connection = apsw.Connection(str(path))
    try:
        connection.execute("DROP TRIGGER invariant_revision_no_update")
        connection.execute(
            "UPDATE invariant_revision SET statement = ? WHERE revision_id = ?",
            ("A statement the base never sealed.", BASE_REVISION_ID),
        )
    finally:
        connection.close()
    connection = apsw.Connection(str(path))
    try:
        connection.execute(
            "CREATE TRIGGER invariant_revision_no_update BEFORE UPDATE ON invariant_revision "
            "BEGIN SELECT RAISE(ABORT, 'immutable_revision: revision rows cannot be updated'); END"
        )
    finally:
        connection.close()


def _reference_case(tmp_path: Path, *, removing: str) -> MergeCase:
    """One side ends without the anchor; the other ends citing it, so the union keeps a dead ref."""

    def base_shape(case: MergeCase, states: dict[str, Path]) -> None:
        add_anchor(states["base"], ANCHOR_ID, "docs/foundation.md")
        del case

    def shape(case: MergeCase, states: dict[str, Path]) -> None:
        delete_anchor(states[removing], ANCHOR_ID)
        citing = "left" if removing == "right" else "right"
        add_realization_claim(states[citing], CLAIM_ID, BASE_REVISION_ID, ANCHOR_ID)
        del case

    return build_case(
        tmp_path,
        diverging_revisions=False,
        diverging_identities=False,
        base_shape=base_shape,
        shape=shape,
    )


def _insert_revision(
    path: Path,
    *,
    repository_id: str,
    revision_id: str,
    statement: str,
    display_version: str = "v2",
) -> None:
    """Insert one whole revision row directly, as two independent authors would."""

    connection = apsw.Connection(str(path))
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.execute(
            "INSERT INTO invariant_revision (repository_id, invariant_id, revision_id, "
            "display_version, statement, applicability, conditions, exclusions, state_at_origin, "
            "acceptance_ref, provenance, payload_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                repository_id,
                BASE_INVARIANT_ID,
                revision_id,
                display_version,
                statement,
                "Every dataset in this case's namespace.",
                "[]",
                "[]",
                "proposed",
                None,
                "{}",
                "a" * 64,
            ),
        )
    finally:
        connection.close()


def _mutate(path: Path, *statements: tuple[str, apsw.Bindings]) -> None:
    """Apply explicit catalog edits to a dataset copy."""

    connection = apsw.Connection(str(path))
    try:
        for statement, parameters in statements:
            connection.execute(statement, parameters)
    finally:
        connection.close()


def _rewrite_schema(path: Path, *, table_type: str, name: str, replace: tuple[str, str]) -> None:
    """Rewrite one catalog object's stored SQL, refusing a replacement that did not apply.

    ``PRAGMA writable_schema`` is the only way to produce a database whose *declared* structure
    disagrees with this schema generation while the changeset machinery would still run happily over
    it, which is exactly the input the preflight exists to catch.
    """

    connection = apsw.Connection(str(path))
    try:
        connection.execute("PRAGMA writable_schema=ON")
        row = next(
            iter(
                connection.execute(
                    "SELECT sql FROM sqlite_schema WHERE type = ? AND name = ?",
                    (table_type, name),
                )
            ),
            None,
        )
        if row is None:
            raise AssertionError(f"the dataset has no {table_type} named {name}")
        original = str(row[0])
        if replace[0] not in original:
            raise AssertionError(f"the {name} definition does not contain {replace[0]!r}")
        connection.execute(
            "UPDATE sqlite_schema SET sql = ? WHERE type = ? AND name = ?",
            (original.replace(*replace), table_type, name),
        )
        connection.execute("PRAGMA writable_schema=OFF")
    finally:
        connection.close()


def _reorder_invariant_columns(path: Path) -> None:
    """Swap two column positions, keeping every name and type so only the order can see it."""

    order = ("repository_id", "invariant_id", "display_label", "label_provenance")
    reordered = ("invariant_id", "repository_id", "display_label", "label_provenance")
    swap: dict[str, str] = dict(zip(order, reordered, strict=True))
    pattern = re.compile("|".join(re.escape(column) for column in order))

    connection = apsw.Connection(str(path))
    try:
        connection.execute("PRAGMA writable_schema=ON")
        row = next(
            iter(
                connection.execute(
                    "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = 'invariant'"
                )
            )
        )
        connection.execute(
            "UPDATE sqlite_schema SET sql = ? WHERE type = 'table' AND name = 'invariant'",
            (pattern.sub(lambda match: swap[match.group(0)], str(row[0])),),
        )
        connection.execute("PRAGMA writable_schema=OFF")
    finally:
        connection.close()


def _commit_on_top(repository: Path, parent: str, name: str, filename: str) -> str:
    """Create one real commit as a child of ``parent`` and return its id."""

    (repository / filename).write_text(name, encoding="utf-8")
    _git(repository, ["add", filename])
    tree = str(_git(repository, ["write-tree"]).stdout.strip())
    commit = str(_git(repository, ["commit-tree", tree, "-p", parent, "-m", name]).stdout.strip())
    (repository / filename).unlink(missing_ok=True)
    _git(repository, ["reset", "--quiet", parent])
    return commit


def _merge_commit(repository: Path, left: str, right: str, message: str) -> str:
    """Create one merge commit with two parents, so two branches share two bases."""

    tree = str(_git(repository, ["rev-parse", f"{left}^{{tree}}"]).stdout.strip())
    return str(
        _git(
            repository, ["commit-tree", tree, "-p", left, "-p", right, "-m", message]
        ).stdout.strip()
    )


def _common_bases(repository: Path, left: str, right: str) -> list[str]:
    """Return every common base commit of two commits, in Git's own order."""

    result = run_git(
        repository,
        ["merge-base", "--all", left, right],
        GitRunnerOptions(identity=dict(GIT_IDENTITY)),
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _git(repository: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    result = run_git(repository, args, GitRunnerOptions(identity=dict(GIT_IDENTITY)))
    if result.returncode:
        raise AssertionError(f"scenario git {args} failed: {result.stderr!r}")
    return result
