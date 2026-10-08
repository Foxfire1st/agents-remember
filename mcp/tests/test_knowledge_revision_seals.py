"""The sealed revision payloads, on both lineage graphs and through the read path.

The predecessor set is inside every revision digest, which is what binds a stored revision identity
to the exact edge set it was authored with. This module owns the executable evidence for that field
on **both** payloads, because neither single-graph module owns the shared mechanism:

* the invariant payload (L1) is exercised for its aggregate round-trip but never for the field
  alone, so a payload that dropped the predecessor set would keep every L1 node green;
* the family payload has the same shape and is sealed by the same mechanism.

Each digest node holds *every other field equal*, and each read node edits the edge **table**
behind a stored row -- the only way to change a sealed predecessor set without rewriting the row
itself -- then asserts that reading the revision back refuses rather than serving a revision whose
identity no longer describes it. One construction node holds what neither aggregate may be built
with at all, because the reader seals whatever value it constructed from the stored row.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.digest import revision_payload_digest
from agents_remember.models.knowledge.family import FamilyRevision
from agents_remember.models.knowledge.invariant import InvariantRevision
from knowledge_fixture_test_support import (
    BASE_CONDITIONS,
    BASE_DISPLAY_VERSION,
    BASE_STATEMENT,
    ESSENTIAL_APPLICABILITY,
    ESSENTIAL_EXCLUSIONS,
    FAMILY_DISPLAY_VERSION,
    FAMILY_GUARANTEE,
    BranchingKnowledgeFixture,
    build_branching_knowledge_fixture,
    make_authorship,
)
from knowledge_rows_test_support import families
from pydantic import ValidationError

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
    """Sealing one identical invariant revision with and without a predecessor changes the digest.

    Only the predecessor set differs between the two seals, and the node asserts that equality
    explicitly, so a payload that omitted the field would seal both to one digest.
    """

    without_edge = InvariantRevision(
        repository_id=fixture.repository_id,
        revision_id=str(uuid4()),
        invariant_id=fixture.invariant_id,
        display_version=BASE_DISPLAY_VERSION,
        statement=BASE_STATEMENT,
        applicability=ESSENTIAL_APPLICABILITY,
        conditions=BASE_CONDITIONS,
        exclusions=ESSENTIAL_EXCLUSIONS,
        provenance=fixture.authorship,
        payload_digest="0" * 64,
    )
    with_edge = without_edge.model_copy(update={"predecessors": (fixture.base_revision_id,)})
    assert without_edge.predecessors == ()
    assert with_edge.model_dump(exclude={"predecessors"}) == without_edge.model_dump(
        exclude={"predecessors"}
    )
    assert revision_payload_digest(without_edge) != revision_payload_digest(with_edge)


def test_neither_revision_aggregate_is_built_from_a_value_that_contradicts_itself() -> None:
    """A predecessor set or an origin state that contradicts itself is refused at construction.

    Both aggregates are what the reader builds from a stored row before it recomputes the seal, so
    each refusal here is a row that is never served: a predecessor named twice would seal one
    authored set to a second digest, a revision cannot precede itself, accepted origin data names
    the acceptance it rests on, and a proposed revision claims none. The accepted control carries a
    predecessor and a reference, so each forgery departs from it in exactly one field.
    """

    repository_id, revision_id, predecessor = str(uuid4()), str(uuid4()), str(uuid4())
    shared: dict[str, Any] = {
        "repository_id": repository_id,
        "revision_id": revision_id,
        "provenance": make_authorship(),
        "predecessors": (predecessor,),
        "state_at_origin": "accepted",
        "acceptance_ref": "developer:accepted",
        "payload_digest": "0" * 64,
    }
    invariant: dict[str, Any] = {
        "invariant_id": str(uuid4()),
        "display_version": BASE_DISPLAY_VERSION,
        "statement": BASE_STATEMENT,
        "applicability": ESSENTIAL_APPLICABILITY,
    }
    family: dict[str, Any] = {
        "family_id": str(uuid4()),
        "display_version": FAMILY_DISPLAY_VERSION,
        "joint_guarantee": FAMILY_GUARANTEE,
    }
    contradictions: dict[str, tuple[dict[str, Any], str]] = {
        "a predecessor named twice": (
            {"predecessors": (predecessor, predecessor)},
            "must not repeat a revision identity",
        ),
        "the revision as its own predecessor": (
            {"predecessors": (revision_id,)},
            "must not declare itself as its own predecessor",
        ),
        "accepted origin data with no acceptance reference": (
            {"acceptance_ref": None},
            "accepted origin data requires a nonempty acceptance_ref",
        ),
        "accepted origin data with a blank acceptance reference": (
            {"acceptance_ref": "  "},
            "accepted origin data requires a nonempty acceptance_ref",
        ),
        "a proposed revision that carries an acceptance reference": (
            {"state_at_origin": "proposed"},
            "a proposed revision must not carry an acceptance_ref",
        ),
    }
    for aggregate, own in ((InvariantRevision, invariant), (FamilyRevision, family)):
        control = aggregate(**shared, **own)
        assert control.predecessors == (predecessor,)
        assert (control.state_at_origin, control.acceptance_ref) == (
            "accepted",
            "developer:accepted",
        )
        for why, (fields, refusal) in contradictions.items():
            with pytest.raises(ValidationError, match=refusal):
                aggregate(**{**shared, **own, **fields})
                pytest.fail(f"{aggregate.__name__} was built from {why}")


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
        assert before.revision.predecessors == (fixture.family.revision_id,)
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
        assert before.revision.predecessors == (fixture.base_revision_id,)
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
