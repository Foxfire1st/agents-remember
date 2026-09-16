"""Boundary cases for the guarded merge: the conflict row identity and the final-integrity checks.

This module holds the cases that need their own scenario rather than another assertion inside an
existing one, so it lives in the integration population -- the unit population is at its declared
ceiling and a new case cannot be added there.

**The row identity a conflict reports.** A changeset supplies only the columns an operation changes,
so the new side of an ``UPDATE`` carries the not-supplied marker where its key columns are, and a key
read from there names no row. The engine does hand the conflict callback the operation it refused,
and its *old* values carry the key. These cases hold that to the two shapes where the earlier
version of this code was wrong: a conflict on a row that is not the first one in the table, and a
table carrying an insert alongside a conflicting update.

**The final-integrity checks and which of them a case can reach.** The merge validates the produced
candidate three ways. The structural check is reachable and has a failing case below: a candidate
carrying a foreign-key violation, built from inputs that all carry it so no delta would otherwise
mention it. The applied-change check and the merged-candidate immutability check cannot be reached
by a black-box case at all -- the merged candidate is a copy of the left side with the right delta
applied, the left side has already passed the immutability comparison, and SQLite's application
cannot drop an operation under this schema. Their docstrings say so; the cases below exercise their
*policies* directly, so the refusals they produce are demonstrated rather than assumed.
"""

from __future__ import annotations

from pathlib import Path

import apsw
import pytest
from agents_remember.application.knowledge_merge import (
    merge_resolved_knowledge_datasets,
    resolve_knowledge_merge_base,
)
from agents_remember.memory.knowledge.merge import KEY_NOT_SUPPLIED, _conflict_record
from agents_remember.memory.knowledge.merge_changeset import AppliedChangeset, build_delta
from agents_remember.memory.knowledge.merge_validation import (
    require_applied_changes,
    require_immutable_revisions_preserved,
)
from agents_remember.models.knowledge.merge import (
    MergeBaseRequest,
    MergeRequest,
    ResolvedGitBase,
)
from agents_remember.models.knowledge.result import KnowledgeRefusal
from merge_case_test_support import (
    BASE_INVARIANT_ID,
    BASE_REVISION_ID,
    MergeCase,
    add_anchor,
    add_invariant,
    build_case,
    copy_closed,
    file_digest,
    row_counts,
    set_label,
)

pytestmark = pytest.mark.integration

OPERATION = "merge_knowledge_datasets"
DANGLING_CLAIM_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
DANGLING_ANCHOR_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


@pytest.fixture
def case(tmp_path: Path) -> MergeCase:
    return build_case(tmp_path)


def resolve(case: MergeCase):
    """Resolve this case's base by ancestry, requiring that the resolution succeeded."""

    world = case.world
    assert world is not None
    outcome = resolve_knowledge_merge_base(
        MergeBaseRequest(
            repository=case.repository,
            git_base=ResolvedGitBase(
                repository_root=world.root,
                base_commit_id=world.base_commit,
                left_commit_id=world.left_commit,
                right_commit_id=world.right_commit,
            ),
            inputs=case.merge_inputs(),
        )
    )
    assert not isinstance(outcome, KnowledgeRefusal), outcome
    return outcome


def run(case: MergeCase):
    """Merge this case's datasets without publishing anything."""

    return merge_resolved_knowledge_datasets(
        MergeRequest(resolution=resolve(case), databases=case.databases_by_role())
    )


def test_a_row_level_conflict_names_the_row_the_engine_refused(tmp_path: Path) -> None:
    """The conflict is on a row that is not the first the table holds.

    Both sides edit the label of an invariant the base already has, and each side also authors its
    own extra invariant. A key taken from the first operation the changeset carries for ``invariant``
    would name the authored row; the engine's own change names the edited one.
    """

    authored: list[str] = []

    def shape(case: MergeCase, states: dict[str, Path]) -> None:
        set_label(states["left"], "the left side's label")
        set_label(states["right"], "the right side's label")
        authored.append(add_invariant(states["left"], case, "the left side's own obligation"))

    case = build_case(tmp_path, diverging_revisions=False, diverging_identities=False, shape=shape)
    before = {role: file_digest(case.state_path(role)) for role in ("base", "left", "right")}
    expected = f"{case.repository.repository_id}/{BASE_INVARIANT_ID}"

    outcome = run(case)

    assert outcome.state == "refused"
    assert outcome.refusal is not None and outcome.refusal.code == "conflicting_values"
    assert outcome.conflict is not None
    assert outcome.conflict.attribution == "engine_attributed"
    assert outcome.conflict.table == "invariant"
    assert outcome.conflict.operation == "UPDATE"
    assert outcome.conflict.record_id == expected
    # The authored row is the table's first operation; the refusal must not name it.
    assert authored and authored[0] not in (outcome.conflict.record_id or expected)
    assert row_counts(case.state_path("left"))["invariant"] == 2
    for role, digest in before.items():
        assert file_digest(case.state_path(role)) == digest


def test_a_table_carrying_an_insert_and_a_conflicting_update_names_the_conflicting_row(
    tmp_path: Path,
) -> None:
    """One table, two operations, one conflict: the record names the operation that conflicted."""

    authored: list[str] = []

    def shape(case: MergeCase, states: dict[str, Path]) -> None:
        set_label(states["left"], "the left side's label")
        set_label(states["right"], "the right side's label")
        authored.append(add_invariant(states["right"], case, "the right side's own obligation"))

    case = build_case(tmp_path, diverging_revisions=False, diverging_identities=False, shape=shape)
    delta = build_delta(case.state_path("right"), case.state_path("base"), side="right")
    operations = [(change.table, change.operation) for change in delta.operations]
    expected = f"{case.repository.repository_id}/{BASE_INVARIANT_ID}"

    outcome = run(case)

    assert ("invariant", "INSERT") in operations and ("invariant", "UPDATE") in operations
    assert outcome.state == "refused"
    assert outcome.refusal is not None and outcome.refusal.code == "conflicting_values"
    assert outcome.conflict is not None
    assert outcome.conflict.record_id == expected
    # The refusal must not name the authored row. Which of the two operations the changeset lists
    # first is an engine detail this case does not depend on, and neither does this assertion: it
    # holds whichever order the insert and the update were materialised in.
    assert authored and authored[0] not in (outcome.conflict.record_id or expected)


def test_the_conflict_record_prefers_the_old_side_and_reports_a_missing_key_as_such() -> None:
    """The key renderer's two facts: the old side wins, and an unreadable key says so.

    An ``INSERT`` is the one operation with no old side, so the new side is the whole row; an
    operation whose change carries no primary-key columns names no row and is reported as naming
    none rather than as some nearby row.
    """

    inserted = AppliedChangeset(
        conflict_code=1,
        conflict_table="invariant",
        conflict_operation="INSERT",
        conflict_key=(BASE_INVARIANT_ID,),
    )
    keyless = AppliedChangeset(
        conflict_code=1, conflict_table="invariant", conflict_operation="UPDATE", conflict_key=None
    )

    assert _conflict_record(inserted) == BASE_INVARIANT_ID
    assert _conflict_record(keyless) == KEY_NOT_SUPPLIED


def test_a_candidate_carrying_a_foreign_key_violation_is_refused_by_the_structural_check(
    tmp_path: Path,
) -> None:
    """The reachable final-integrity guard: the produced candidate is not structurally valid.

    All three inputs carry the same dangling reference, so it appears in neither delta and nothing
    earlier refuses it: identity admission reads rows, the preflight reads the catalog, and the
    coverage replay compares logical bodies -- none of which inspects foreign keys. The candidate is
    therefore the first place it can be seen, which is exactly what the check is for.
    """

    def base_shape(case: MergeCase, states: dict[str, Path]) -> None:
        _add_dangling_claim(states["base"], case.repository.repository_id)

    def shape(case: MergeCase, states: dict[str, Path]) -> None:
        del case
        add_anchor(states["left"], "cccccccc-cccc-4ccc-8ccc-cccccccccccc", "docs/left.md")
        add_anchor(states["right"], "dddddddd-dddd-4ddd-8ddd-dddddddddddd", "docs/right.md")

    case = build_case(
        tmp_path,
        diverging_revisions=False,
        diverging_identities=False,
        base_shape=base_shape,
        shape=shape,
    )
    for role in ("base", "left", "right"):
        reader = apsw.Connection(str(case.state_path(role)))
        try:
            assert len(list(reader.execute("PRAGMA foreign_key_check"))) == 1, role
        finally:
            reader.close()

    outcome = run(case)

    assert outcome.state == "refused"
    assert outcome.merged_identity is None
    assert outcome.refusal is not None
    assert outcome.refusal.code == "changeset_postcondition_failed"
    assert "foreign-key violation" in outcome.refusal.detail


def test_a_candidate_that_dropped_an_intended_change_is_refused(case: MergeCase) -> None:
    """The applied-change policy, exercised directly because its call site cannot be reached.

    The candidate is a copy of the left side: structurally valid, preserving the base's sealed
    aggregates, and missing the change the right delta carried. That is the state an application
    which reported success while dropping an operation would leave behind.
    """

    delta = build_delta(case.state_path("right"), case.state_path("base"), side="right")
    lacking = copy_closed(
        case.state_path("left"), case.state_path("left").parent / "lacking.sqlite"
    )

    refusal = require_applied_changes(
        OPERATION, merged=lacking, side=case.state_path("right"), operations=delta.operations
    )

    assert refusal is not None
    assert refusal.code == "changeset_postcondition_failed"
    assert refusal.table in {"invariant", "invariant_revision"}
    assert refusal.record_id is not None


def test_a_candidate_missing_a_sealed_aggregate_is_refused(case: MergeCase) -> None:
    """The merged-candidate immutability policy, exercised directly for the same reason.

    The candidate is a copy of the left side with one sealed revision removed the way only a
    damaged file could lose it -- the declared triggers are put back afterwards, so what is under
    test is the comparison and not the schema.
    """

    lacking = copy_closed(
        case.state_path("left"), case.state_path("left").parent / "revision-removed.sqlite"
    )
    _remove_sealed_revision(lacking, BASE_REVISION_ID)

    refusal = require_immutable_revisions_preserved(
        OPERATION, base=case.state_path("base"), candidate=lacking, role="merged candidate"
    )

    assert refusal is not None
    assert refusal.code == "immutable_revision_changed"
    assert refusal.table == "invariant_revision"
    assert refusal.record_id == BASE_REVISION_ID


def _add_dangling_claim(path: Path, repository_id: str) -> None:
    """Insert a realization claim whose anchor does not exist, with FK enforcement off."""

    connection = apsw.Connection(str(path))
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "INSERT INTO realization_claim (repository_id, claim_id, invariant_revision_id, "
            "anchor_id, role, rationale, provenance) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                repository_id,
                DANGLING_CLAIM_ID,
                BASE_REVISION_ID,
                DANGLING_ANCHOR_ID,
                "supports",
                "a claim on an anchor that is not there",
                "{}",
            ),
        )
    finally:
        connection.close()


def _remove_sealed_revision(path: Path, revision_id: str) -> None:
    """Delete one sealed revision row, restoring the trigger that refuses the delete."""

    connection = apsw.Connection(str(path))
    try:
        connection.execute("DROP TRIGGER invariant_revision_no_delete")
        connection.execute("DELETE FROM invariant_revision WHERE revision_id = ?", (revision_id,))
    finally:
        connection.close()
    connection = apsw.Connection(str(path))
    try:
        connection.execute(
            "CREATE TRIGGER invariant_revision_no_delete BEFORE DELETE ON invariant_revision "
            "BEGIN SELECT RAISE(ABORT, 'immutable_revision: revision rows cannot be deleted'); END"
        )
    finally:
        connection.close()
