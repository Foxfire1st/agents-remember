"""The sealed revision payloads, on both lineage graphs and through the read path.

The predecessor set is inside every revision digest, which is what binds a stored revision identity
to the exact edge set it was authored with. This module owns the executable evidence for that field
on **both** payloads, because neither single-graph module owns the shared mechanism:

* the invariant payload (L1) is exercised for its aggregate round-trip but never for the field
  alone, so a payload that dropped the predecessor set would keep every L1 node green;
* the family payload has the same shape, and its seal node lives in
  ``test_knowledge_family_revision.py`` next to the revision rules it belongs to.

Each digest node holds *every other field equal*, and each read node edits the edge **table**
behind a stored row -- the only way to change a sealed predecessor set without rewriting the row
itself -- then asserts that reading the revision back refuses rather than serving a revision whose
identity no longer describes it.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from agents_remember.memory.knowledge import families, records
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.result import RevisionDraft
from knowledge_fixture_test_support import (
    BASE_CONDITIONS,
    BASE_DISPLAY_VERSION,
    BASE_STATEMENT,
    ESSENTIAL_APPLICABILITY,
    ESSENTIAL_EXCLUSIONS,
    BranchingKnowledgeFixture,
    build_branching_knowledge_fixture,
)

_FAMILY_EDGE_INSERT = (
    "INSERT INTO family_predecessor "
    "(repository_id, family_id, child_revision_id, parent_revision_id) VALUES (?, ?, ?, ?)"
)

_FAMILY_EDGE_DELETE = (
    "DELETE FROM family_predecessor WHERE repository_id = ? AND family_id = ? "
    "AND child_revision_id = ? AND parent_revision_id = ?"
)

_INVARIANT_EDGE_INSERT = (
    "INSERT INTO invariant_predecessor "
    "(repository_id, invariant_id, child_revision_id, parent_revision_id) VALUES (?, ?, ?, ?)"
)

_INVARIANT_EDGE_DELETE = (
    "DELETE FROM invariant_predecessor WHERE repository_id = ? AND invariant_id = ? "
    "AND child_revision_id = ? AND parent_revision_id = ?"
)


@pytest.fixture
def fixture(tmp_path: Path) -> BranchingKnowledgeFixture:
    return build_branching_knowledge_fixture(tmp_path / "seals")


def test_an_invariant_revision_digest_seals_its_predecessor_set(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """Sealing one identical invariant draft twice, with and without a predecessor, changes the digest.

    The invariant half of the same check the family module makes: only the predecessor set differs
    between the two seals, and the node asserts that equality explicitly, so a payload that omitted
    the field would seal both drafts to one digest.
    """

    draft = RevisionDraft(
        revision_id=str(uuid4()),
        invariant_id=fixture.invariant_id,
        display_version=BASE_DISPLAY_VERSION,
        statement=BASE_STATEMENT,
        applicability=ESSENTIAL_APPLICABILITY,
        conditions=BASE_CONDITIONS,
        exclusions=ESSENTIAL_EXCLUSIONS,
        provenance=fixture.authorship,
    )
    without_edge = records.sealed_revision_from_draft(fixture.repository_id, draft)
    with_edge = records.sealed_revision_from_draft(
        fixture.repository_id,
        draft.model_copy(update={"predecessors": (fixture.base_revision_id,)}),
    )
    assert without_edge.predecessors == ()
    assert with_edge.predecessors == (fixture.base_revision_id,)
    assert with_edge.model_dump(
        exclude={"payload_digest", "predecessors"}
    ) == without_edge.model_dump(exclude={"payload_digest", "predecessors"})
    assert without_edge.payload_digest != with_edge.payload_digest


def test_a_family_revision_read_refuses_after_a_predecessor_edge_is_added(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """An edge added behind a sealed family revision is detected when the revision is read back.

    The seal covers the edge set, so an edge can only be added without changing the stored row by
    writing the edge table directly -- what a changeset, a repair script or a future operation that
    forgot to re-seal would do. The read must refuse instead of serving the revision as if its
    identity still described it.
    """

    with fixture.reopen() as store:
        before = families.get_family_revision(store, fixture.family_sibling_revision_id)
        assert before is not None
        assert before.predecessors_sorted == (fixture.family.revision_id,)
        store.connection.execute(
            _FAMILY_EDGE_INSERT,
            (
                fixture.repository_id,
                fixture.family.family_id,
                fixture.family_sibling_revision_id,
                fixture.family_successor_revision_id,
            ),
        )
        with pytest.raises(KnowledgeStorageError, match="payload digest"):
            families.get_family_revision(store, fixture.family_sibling_revision_id)


def test_a_family_revision_read_refuses_after_a_predecessor_edge_is_deleted(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """An edge deleted behind a sealed family revision is detected when the revision is read back.

    The delete trigger is dropped first because the point of the case is the *second* defence: even
    a database edited by something that bypassed the operation must not be served as the revision
    the identity names.
    """

    with fixture.reopen() as store:
        store.connection.execute("DROP TRIGGER family_predecessor_no_delete")
        store.connection.execute(
            _FAMILY_EDGE_DELETE,
            (
                fixture.repository_id,
                fixture.family.family_id,
                fixture.family_successor_revision_id,
                fixture.family.revision_id,
            ),
        )
        with pytest.raises(KnowledgeStorageError, match="payload digest"):
            families.get_family_revision(store, fixture.family_successor_revision_id)


def test_an_invariant_revision_read_refuses_after_a_predecessor_edge_is_added(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """The invariant read path detects an edge added behind a sealed revision."""

    with fixture.reopen() as store:
        before = store.get_revision(fixture.right_revision_id)
        assert before is not None
        assert before.predecessors_sorted == (fixture.base_revision_id,)
        store.connection.execute(
            _INVARIANT_EDGE_INSERT,
            (
                fixture.repository_id,
                fixture.invariant_id,
                fixture.right_revision_id,
                fixture.left_revision_id,
            ),
        )
        with pytest.raises(KnowledgeStorageError, match="payload digest"):
            store.get_revision(fixture.right_revision_id)


def test_an_invariant_revision_read_refuses_after_a_predecessor_edge_is_deleted(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """The invariant read path detects an edge removed behind a sealed revision."""

    with fixture.reopen() as store:
        store.connection.execute("DROP TRIGGER invariant_predecessor_no_delete")
        store.connection.execute(
            _INVARIANT_EDGE_DELETE,
            (
                fixture.repository_id,
                fixture.invariant_id,
                fixture.left_revision_id,
                fixture.base_revision_id,
            ),
        )
        with pytest.raises(KnowledgeStorageError, match="payload digest"):
            store.get_revision(fixture.left_revision_id)
