"""Focused behaviour of the two standalone label operations and their concurrency guard.

A label is the one mutable field of an identity row, which is why a label edit is the only identity
edit the store exposes and why it names the row the caller read. Each case here protects one
consequential outcome: the edit that writes and reads back, the stale expectation that must not
overwrite a row that moved under the caller, and the unknown identity that must not invent one.

These operations are separate from the batch path on purpose -- the batch carries its own copy of
the expectation check inside its preconditions -- so a case has to drive the standalone entry point
for the guard in :mod:`agents_remember.memory.knowledge.labels` to be load-bearing at all.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from agents_remember.application.knowledge import (
    admitted_knowledge_destination,
    set_knowledge_family_label,
    set_knowledge_invariant_label,
)
from agents_remember.memory.knowledge import families
from agents_remember.models.knowledge.context import AdmittedKnowledgeDestination
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import (
    SetFamilyLabelRequest,
    SetInvariantLabelRequest,
)
from knowledge_fixture_test_support import (
    BranchingKnowledgeFixture,
    build_branching_knowledge_fixture,
    make_authorship,
)

pytestmark = pytest.mark.evidence_unit


@pytest.fixture
def fixture(tmp_path: Path) -> BranchingKnowledgeFixture:
    return build_branching_knowledge_fixture(tmp_path / "candidate")


def destination(fixture: BranchingKnowledgeFixture) -> AdmittedKnowledgeDestination:
    """Return the admitted destination for the fixture's candidate database."""

    return admitted_knowledge_destination(
        fixture.database_path,
        repository=RepositoryIdentity(
            repository_id=fixture.repository_id, authority_home="agents-remember"
        ),
        authorship=make_authorship(),
    )


def test_an_invariant_label_edit_writes_and_reads_back(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """A label edit against the row the caller read returns ``labeled`` and stores the new label."""

    store = fixture.reopen()
    try:
        before = store.get_invariant(fixture.invariant_id)
        assert before is not None
        expected_row_digest = before.row_digest
    finally:
        store.close()

    result = set_knowledge_invariant_label(
        destination(fixture),
        SetInvariantLabelRequest(
            repository_id=fixture.repository_id,
            invariant_id=fixture.invariant_id,
            display_label="relabelled invariant",
            expected_row_digest=expected_row_digest,
        ),
    )

    assert result.state == "labeled"
    assert result.display_label == "relabelled invariant"
    assert result.refusal is None

    store = fixture.reopen()
    try:
        after = store.get_invariant(fixture.invariant_id)
        assert after is not None
        assert after.display_label == "relabelled invariant"
        assert after.row_digest != expected_row_digest
    finally:
        store.close()


def test_a_stale_invariant_label_expectation_refuses_and_leaves_the_row_identical(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """A label edit whose expected row digest is stale refuses ``stale_precondition`` and writes nothing.

    The row is read before and after the refusal and compared by digest, so what proves the guard is
    the stored row rather than the operation's own report. Without the expectation check this case
    would silently overwrite a label the caller never read.
    """

    store = fixture.reopen()
    try:
        before = store.get_invariant(fixture.invariant_id)
        assert before is not None
        row_digest_before = before.row_digest
        label_before = before.display_label
    finally:
        store.close()

    result = set_knowledge_invariant_label(
        destination(fixture),
        SetInvariantLabelRequest(
            repository_id=fixture.repository_id,
            invariant_id=fixture.invariant_id,
            display_label="must not be written",
            expected_row_digest="0" * 64,
        ),
    )

    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "stale_precondition"
    assert result.refusal.table == "invariant"
    assert result.refusal.record_id == fixture.invariant_id
    assert result.refusal.expected == "0" * 64
    assert result.refusal.observed == row_digest_before

    store = fixture.reopen()
    try:
        after = store.get_invariant(fixture.invariant_id)
        assert after is not None
        assert after.row_digest == row_digest_before
        assert after.display_label == label_before
    finally:
        store.close()


def test_an_unknown_invariant_label_target_refuses_without_inventing_a_row(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """A label edit for an identity the namespace does not hold refuses ``unknown_invariant``."""

    missing = str(uuid4())
    result = set_knowledge_invariant_label(
        destination(fixture),
        SetInvariantLabelRequest(
            repository_id=fixture.repository_id,
            invariant_id=missing,
            display_label="no such invariant",
            expected_row_digest="0" * 64,
        ),
    )

    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "unknown_invariant"
    assert result.refusal.record_id == missing

    store = fixture.reopen()
    try:
        assert store.get_invariant(missing) is None
    finally:
        store.close()


def test_a_family_label_edit_writes_and_reads_back(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """The family twin writes, returns ``labeled`` and reads back the stored label."""

    store = fixture.reopen()
    try:
        before = families.get_family(store, fixture.family.family_id)
        assert before is not None
        expected_row_digest = before.row_digest
    finally:
        store.close()

    result = set_knowledge_family_label(
        destination(fixture),
        SetFamilyLabelRequest(
            repository_id=fixture.repository_id,
            family_id=fixture.family.family_id,
            display_label="relabelled family",
            expected_row_digest=expected_row_digest,
        ),
    )

    assert result.state == "labeled"
    assert result.display_label == "relabelled family"

    store = fixture.reopen()
    try:
        after = families.get_family(store, fixture.family.family_id)
        assert after is not None
        assert after.display_label == "relabelled family"
        assert after.row_digest != expected_row_digest
    finally:
        store.close()


def test_a_stale_family_label_expectation_refuses_and_leaves_the_row_identical(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """The family twin refuses a stale expectation and leaves its row byte-identical."""

    store = fixture.reopen()
    try:
        before = families.get_family(store, fixture.family.family_id)
        assert before is not None
        row_digest_before = before.row_digest
        label_before = before.display_label
    finally:
        store.close()

    result = set_knowledge_family_label(
        destination(fixture),
        SetFamilyLabelRequest(
            repository_id=fixture.repository_id,
            family_id=fixture.family.family_id,
            display_label="must not be written",
            expected_row_digest="1" * 64,
        ),
    )

    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "stale_precondition"
    assert result.refusal.record_id == fixture.family.family_id
    assert result.refusal.observed == row_digest_before

    store = fixture.reopen()
    try:
        after = families.get_family(store, fixture.family.family_id)
        assert after is not None
        assert after.row_digest == row_digest_before
        assert after.display_label == label_before
    finally:
        store.close()


def test_an_unknown_family_label_target_refuses_without_inventing_a_row(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """A label edit for a family the namespace does not hold refuses ``unknown_family``."""

    missing = str(uuid4())
    result = set_knowledge_family_label(
        destination(fixture),
        SetFamilyLabelRequest(
            repository_id=fixture.repository_id,
            family_id=missing,
            display_label="no such family",
            expected_row_digest="0" * 64,
        ),
    )

    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "unknown_family"

    store = fixture.reopen()
    try:
        assert families.get_family(store, missing) is None
    finally:
        store.close()
