"""The sealed revision payloads, at the model and through the read path.

A revision's ``payload_digest`` seals its stored row: a row whose text was rewritten behind its
identity is refused on decode rather than served as the revision that identity names. This module
owns the executable evidence for that seal on **both** revision payloads:

* the digest covers the stored cells (the seal unit test pins one field's contribution);
* the aggregates refuse self-contradictory construction (a predecessor named twice, a revision as
  its own predecessor, acceptance mismatches);
* each read node rewrites one stored cell behind a sealed row -- the only way to change sealed
  content without rewriting the row through the writer, which recomputes the seal -- then asserts
  that decoding the row refuses rather than serving content its identity no longer describes.

The predecessor edge tables are gone with the schema (MIK-R74 rule 1): there is no edge table
left to tamper with, and decoded predecessors are always ().
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import apsw
import pytest
from agents_remember.memory.knowledge.records import (
    decode_family_revision_row,
    decode_revision_row,
)
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


def test_an_invariant_revision_read_refuses_a_cell_rewritten_behind_its_seal(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """The invariant read path detects a stored cell rewritten behind a sealed revision.

    The seal covers the row's cells, so content can only change without changing the stored
    row by rewriting the cell directly -- what a repair script or a future operation that
    forgot to re-seal would do. The decode must refuse instead of serving the revision as if
    its identity still described it.
    """

    with fixture.reopen() as store:
        before = store.get_revision(fixture.third_revision_id)
        assert before is not None
        assert before.revision.predecessors == ()
    tamperer = apsw.Connection(str(fixture.database_path))
    try:
        row = list(
            next(
                tamperer.execute(
                    "SELECT repository_id, invariant_id, revision_id, display_version, statement, "
                    "applicability, conditions, exclusions, state_at_origin, acceptance_ref, "
                    "provenance, payload_digest FROM invariant_revision WHERE revision_id = ?",
                    (fixture.third_revision_id,),
                )
            )
        )
    finally:
        tamperer.close()
    row[4] = "A statement rewritten behind the identity."
    with pytest.raises(KnowledgeStorageError, match="payload digest"):
        decode_revision_row(row, ())


def test_a_family_revision_read_refuses_a_cell_rewritten_behind_its_seal(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """The family read path detects a stored cell rewritten behind a sealed revision."""

    with fixture.reopen() as store:
        before = families.get_family_revision(store, fixture.overlapping_family.revision_id)
        assert before is not None
        assert before.revision.predecessors == ()
    tamperer = apsw.Connection(str(fixture.database_path))
    try:
        row = list(
            next(
                tamperer.execute(
                    "SELECT repository_id, family_id, revision_id, display_version, joint_guarantee, "
                    "state_at_origin, acceptance_ref, provenance, payload_digest FROM family_revision "
                    "WHERE revision_id = ?",
                    (fixture.overlapping_family.revision_id,),
                )
            )
        )
    finally:
        tamperer.close()
    row[4] = "A guarantee rewritten behind the identity."
    with pytest.raises(KnowledgeStorageError, match="payload digest"):
        decode_family_revision_row(row, ())
