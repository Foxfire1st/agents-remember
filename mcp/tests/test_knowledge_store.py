"""Focused behaviour of the immutable knowledge identity store.

Each case protects one consequential operation or failure: a divergent-successor read, an
identity reuse, a lineage violation, or a database-level immutability guarantee. Constructor
validation is not re-tested here; the failure each case protects is a stored-state failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import apsw
import pytest
from agents_remember.application.knowledge import (
    admitted_knowledge_destination,
    admitted_revision_request,
    create_knowledge_revision,
    initialize_knowledge_namespace,
    open_admitted_knowledge_store,
    write_authorship,
)
from agents_remember.memory.knowledge import (
    logical,
    open_existing_knowledge_store,
    open_knowledge_store,
)
from agents_remember.memory.knowledge.records import revision_row, sealed_revision_from_draft
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError, lineage_cycle_refusal
from agents_remember.memory.knowledge.schema_generations import (
    CURRENT_GENERATION,
    GENERATION_1,
)
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.invariant import InvariantRevision
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import (
    InvariantRequest,
    RevisionDraft,
    RevisionRequest,
)
from generation_test_support import create_generation_1_store, declared_pair
from knowledge_fixture_test_support import (
    BASE_DISPLAY_VERSION,
    BASE_STATEMENT,
    BRANCH_A_STATEMENT,
    BRANCH_B_STATEMENT,
    SHARED_SUCCESSOR_DISPLAY_VERSION,
    BranchingKnowledgeFixture,
    build_branching_knowledge_fixture,
    fixture_revision_draft,
    make_authorship,
)


@pytest.fixture
def fixture(tmp_path: Path) -> BranchingKnowledgeFixture:
    return build_branching_knowledge_fixture(tmp_path / "candidate")


@dataclass(frozen=True)
class ExtensionOptions:
    """The optional variations an extension case applies to one fixture revision."""

    invariant_id: str | None = None
    predecessors: tuple[str, ...] = ()
    statement: str = "A further authored obligation."
    provenance: Authorship | None = None


def _extension_request(
    fixture: BranchingKnowledgeFixture, revision_id: str, options: ExtensionOptions | None = None
) -> RevisionRequest:
    resolved = options or ExtensionOptions()
    draft = fixture_revision_draft(
        fixture,
        revision_id,
        statement=resolved.statement,
        predecessors=resolved.predecessors,
    )
    if resolved.invariant_id is not None:
        draft = draft.model_copy(update={"invariant_id": resolved.invariant_id})
    if resolved.provenance is not None:
        draft = draft.model_copy(update={"provenance": resolved.provenance})
    return RevisionRequest(repository_id=fixture.repository_id, revision=draft)


def test_two_same_label_successors_reopen_as_separate_revisions(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """Both v2 successors stay independently addressable with their exact statements.

    The failure this catches is the one the requirement exists for: a store that treats a
    display version as identity, or that keeps only the newest revision, would answer both
    identities with one statement.
    """

    with fixture.reopen() as store:
        base = store.get_revision(fixture.base_revision_id)
        left = store.get_revision(fixture.left_revision_id)
        right = store.get_revision(fixture.right_revision_id)
    assert base is not None and left is not None and right is not None
    assert left.revision.display_version == SHARED_SUCCESSOR_DISPLAY_VERSION
    assert right.revision.display_version == SHARED_SUCCESSOR_DISPLAY_VERSION
    assert left.revision.statement == BRANCH_A_STATEMENT
    assert right.revision.statement == BRANCH_B_STATEMENT
    assert left.revision.revision_id != right.revision.revision_id
    assert left.predecessors_sorted == right.predecessors_sorted == (fixture.base_revision_id,)
    assert base.revision.display_version == BASE_DISPLAY_VERSION
    assert base.revision.statement == BASE_STATEMENT
    assert base.revision.provenance == fixture.authorship
    assert left.revision.provenance == fixture.authorship


def test_reopening_the_same_path_keeps_identity_and_schema(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """A reopen resolves the same identities, digests and schema generation.

    Re-scoped by `KS-R10` §Shipped Assertions: a created store reports the generation *creation
    declared* (requirement 2.7), which is the newest generation this build supports -- generation 2
    after this leaf -- rather than the build's own generation 1. The assertion therefore reads the
    created generation's recorded pair and fingerprint. The generation-1 fact is asserted by
    :func:`test_a_version_1_candidate_stays_version_1_when_generation_2_code_opens_it`, which
    brings a version-1 dataset into being and opens it.
    """

    with fixture.reopen() as first:
        digests = {
            revision_id: first.get_revision(revision_id).revision.payload_digest  # type: ignore[union-attr]
            for revision_id in (
                fixture.base_revision_id,
                fixture.left_revision_id,
                fixture.right_revision_id,
            )
        }
        fingerprint = first.schema.fingerprint
    with open_existing_knowledge_store(fixture.database_path, fixture.repository_id) as second:
        assert second.schema.fingerprint == fingerprint
        assert second.schema.schema_name == CURRENT_GENERATION.schema_name
        assert second.schema.user_version == CURRENT_GENERATION.user_version
        assert second.schema.fingerprint == CURRENT_GENERATION.fingerprint
        for revision_id, digest in digests.items():
            stored = second.get_revision(revision_id)
            assert stored is not None
            assert stored.revision.payload_digest == digest


def test_a_version_1_candidate_stays_version_1_when_generation_2_code_opens_it(
    tmp_path: Path,
) -> None:
    """Requirement 5.1: an in-flight version-1 dataset keeps its own generation and its identity.

    The store is created through generation 1's own recorded DDL, so it is a genuine version-1
    dataset: its recorded version is not rewritten, it validates against generation 1's tables,
    columns and triggers, and it reports generation 1's recorded fingerprint rather than the running
    build's.
    """

    repository_id = str(uuid4())
    with create_generation_1_store(tmp_path / "in-flight-v1.db", repository_id) as store:
        assert store.schema.schema_name == GENERATION_1.schema_name
        assert store.schema.user_version == GENERATION_1.user_version
        assert store.schema.fingerprint == GENERATION_1.fingerprint
        assert declared_pair(store.database_path) == (
            GENERATION_1.schema_name,
            GENERATION_1.user_version,
        )
        assert store.snapshot_identity().schema_version == GENERATION_1.schema_name
        # The dataset digests under generation 1, including the fields that would otherwise move
        # with the build: its own schema name, its own user_version and its own fingerprint.
        body = logical.logical_body(store.connection, GENERATION_1)
        assert body["schema"] == GENERATION_1.schema_name
        assert body["user_version"] == GENERATION_1.user_version
        assert body["schema_fingerprint"] == GENERATION_1.fingerprint
        assert tuple(body["tables"]) == GENERATION_1.tables


def test_a_repeated_identical_invariant_is_no_change_and_a_relabel_refuses(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """``create_invariant`` confirms an identical repeat and refuses a different label under one id.

    This is the identity operation's own contract as the earlier leaf published it, and it is pinned
    here because the difference between the two outcomes is the whole reason the store distinguishes
    "you already did this" from "you are trying to change a stored identity". The sibling concepts
    (family, family revision, membership, realization, anchor) answer the same way; a shared insert
    helper must not quietly unify them into one stricter rule.
    """

    store = fixture.reopen()
    try:
        stored = store.get_invariant(fixture.invariant_id)
        assert stored is not None
        request = InvariantRequest(
            repository_id=fixture.repository_id,
            invariant_id=fixture.invariant_id,
            display_label=stored.display_label,
            provenance=fixture.authorship,
        )
        repeated = store.create_invariant(request)
        relabelled = store.create_invariant(
            request.model_copy(update={"display_label": "a different label for a stored identity"})
        )
        still_stored = store.get_invariant(fixture.invariant_id)
    finally:
        store.close()

    assert repeated.state == "no_change"
    assert repeated.stored is False
    assert repeated.refusal is None
    assert relabelled.state == "refused"
    assert relabelled.refusal is not None
    assert relabelled.refusal.code == "duplicate_identity"
    assert still_stored is not None
    assert still_stored.display_label == stored.display_label


def test_created_revision_stores_the_digest_the_store_recomputed(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """The digest in the result is the seal the store itself recomputed and stored."""

    request = _extension_request(
        fixture,
        str(uuid4()),
        ExtensionOptions(predecessors=(fixture.base_revision_id,)),
    )
    with fixture.reopen() as store:
        result = store.create_revision(request)
        stored = store.get_revision(result.revision_id)
    assert result.state == "created"
    assert stored is not None
    assert stored.revision.payload_digest == result.payload_digest
    assert len(result.payload_digest or "") == 64


def test_identical_aggregate_is_no_change(fixture: BranchingKnowledgeFixture) -> None:
    """Re-submitting one exact aggregate reports no change rather than a second row."""

    request = _extension_request(
        fixture,
        str(uuid4()),
        ExtensionOptions(predecessors=(fixture.base_revision_id,)),
    )
    with fixture.reopen() as store:
        assert store.create_revision(request).state == "created"
        repeated = store.create_revision(request)
        revisions = store.list_revision_ids(fixture.invariant_id)
    assert repeated.state == "no_change"
    assert repeated.stored is False
    assert repeated.payload_digest is not None
    assert len(revisions) == 4


def test_reused_revision_identity_with_other_content_refuses(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """An identity reuse leaves the stored revision intact and names both digests."""

    with fixture.reopen() as store:
        before = store.get_revision(fixture.left_revision_id)
        result = store.create_revision(
            _extension_request(
                fixture,
                fixture.left_revision_id,
                ExtensionOptions(
                    predecessors=(fixture.base_revision_id,),
                    statement="A rewrite of an already cited revision.",
                ),
            )
        )
        after = store.get_revision(fixture.left_revision_id)
    assert before is not None and after is not None
    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "duplicate_identity"
    assert result.refusal.expected == before.revision.payload_digest
    assert result.refusal.observed not in (None, result.refusal.expected)
    assert after == before
    assert after.revision.statement == BRANCH_A_STATEMENT


def test_accepted_origin_data_requires_an_acceptance_reference(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """Acceptance is authored provenance, so accepted state without its reference refuses."""

    with fixture.reopen() as store:
        stored = store.get_revision(fixture.base_revision_id)
    assert stored is not None
    payload = stored.revision.model_dump()
    with pytest.raises(ValueError, match="acceptance_ref"):
        InvariantRevision(**{**payload, "state_at_origin": "accepted"})


def test_cross_invariant_predecessor_refuses_with_the_named_endpoint(
    tmp_path: Path,
) -> None:
    """A predecessor belonging to another invariant refuses and stores nothing."""

    fixture = build_branching_knowledge_fixture(tmp_path / "cross", database_name="cross.db")
    other_invariant = str(uuid4())
    with fixture.reopen() as store:
        created = store.create_invariant(
            InvariantRequest(
                repository_id=fixture.repository_id,
                invariant_id=other_invariant,
                display_label="a second invariant",
                provenance=make_authorship(),
            )
        )
        result = store.create_revision(
            _extension_request(
                fixture,
                str(uuid4()),
                ExtensionOptions(
                    invariant_id=other_invariant,
                    predecessors=(fixture.base_revision_id,),
                    statement="A successor reaching into another invariant's lineage.",
                ),
            )
        )
        revisions = store.list_revision_ids(other_invariant)
    assert created.state == "created"
    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "invalid_reference"
    assert result.refusal.record_id == fixture.base_revision_id
    assert result.refusal.expected == other_invariant
    assert result.refusal.observed == fixture.invariant_id
    assert revisions == ()


def test_dangling_predecessor_refuses_without_leaving_a_row(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """A predecessor that was never authored refuses and stores nothing."""

    missing = str(uuid4())
    with fixture.reopen() as store:
        before = store.list_revision_ids(fixture.invariant_id)
        result = store.create_revision(
            _extension_request(
                fixture,
                str(uuid4()),
                ExtensionOptions(
                    predecessors=(missing,),
                    statement="A successor of a revision that does not exist.",
                ),
            )
        )
        after = store.list_revision_ids(fixture.invariant_id)
    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "invalid_reference"
    assert result.refusal.record_id == missing
    assert after == before


def test_self_predecessor_is_refused_at_the_vocabulary_boundary(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """A revision declaring itself is refused before it can reach the store.

    The refusal lives in the vocabulary because it is a property of the payload, not of the
    database: a self-referencing aggregate is never a well-formed authored value, so it must
    not depend on a reachability query having run.
    """

    revision_id = str(uuid4())
    with pytest.raises(ValueError, match="own predecessor"):
        _extension_request(
            fixture,
            revision_id,
            ExtensionOptions(
                predecessors=(revision_id,), statement="A revision that declares itself."
            ),
        )
    with fixture.reopen() as store:
        assert revision_id not in store.list_revision_ids(fixture.invariant_id)


def _revision_count(store: object) -> int:
    connection = store.connection  # type: ignore[attr-defined]
    return int(next(iter(connection.execute("SELECT count(*) FROM invariant_revision")))[0])


def _edge_count(store: object) -> int:
    connection = store.connection  # type: ignore[attr-defined]
    return int(next(iter(connection.execute("SELECT count(*) FROM invariant_predecessor")))[0])


def _disable_immutability_triggers(store: object) -> None:
    """Drop the write-time triggers so a case can construct a state the operation forbids.

    Dropping them is what makes the *second* defence the subject of the case: the operation's own
    preconditions are bypassed deliberately, so the structural check has to be the thing that
    detects the damage.
    """

    connection = store.connection  # type: ignore[attr-defined]
    for trigger in (
        "invariant_revision_no_update",
        "invariant_revision_no_delete",
        "invariant_predecessor_no_update",
        "invariant_predecessor_no_delete",
    ):
        connection.execute(f"DROP TRIGGER {trigger}")


def _write_raw_revision_row(
    store: object, fixture: BranchingKnowledgeFixture, revision_id: str, statement: str
) -> None:
    """Write one revision row with no predecessors, bypassing triggers and the operation."""

    connection = store.connection  # type: ignore[attr-defined]
    sealed = sealed_revision_from_draft(
        fixture.repository_id,
        fixture_revision_draft(fixture, revision_id, statement=statement, predecessors=()),
    )
    connection.execute(
        "INSERT INTO invariant_revision "
        "(repository_id, invariant_id, revision_id, display_version, statement, applicability, "
        "conditions, exclusions, state_at_origin, acceptance_ref, provenance, payload_digest) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        revision_row(sealed),
    )


def _write_raw_edge(
    store: object, fixture: BranchingKnowledgeFixture, child_id: str, parent_id: str
) -> None:
    """Write one predecessor edge directly, bypassing the triggers."""

    connection = store.connection  # type: ignore[attr-defined]
    connection.execute(
        "INSERT INTO invariant_predecessor "
        "(repository_id, invariant_id, child_revision_id, parent_revision_id) VALUES (?, ?, ?, ?)",
        (fixture.repository_id, fixture.invariant_id, child_id, parent_id),
    )


def _write_raw_cycle(
    store: object, fixture: BranchingKnowledgeFixture, first_id: str, second_id: str
) -> None:
    """Write a two-revision cycle directly, bypassing the triggers and the operation."""

    _write_raw_revision_row(store, fixture, first_id, "The first record of a two-record cycle.")
    _write_raw_revision_row(store, fixture, second_id, "The second record of a two-record cycle.")
    _write_raw_edge(store, fixture, first_id, second_id)
    _write_raw_edge(store, fixture, second_id, first_id)


def test_cycle_membership_reports_the_edges_of_an_on_cycle_revision(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """The membership query reports both edges of a cycle whose member is the queried revision.

    The cyclic state is written outside the operation -- the admission rule gives a cycle no
    reachable creation path through it -- so the membership query is exercised against a graph
    the operation would never produce.
    """

    first_id, second_id = str(uuid4()), str(uuid4())
    with fixture.reopen() as store:
        _disable_immutability_triggers(store)
        _write_raw_cycle(store, fixture, first_id, second_id)
        on_cycle = sealed_revision_from_draft(
            fixture.repository_id,
            fixture_revision_draft(
                fixture,
                first_id,
                statement="The first record of a two-record cycle.",
                predecessors=(second_id,),
            ),
        )
        members = store.lineage_cycle_members(on_cycle)
        admitted = store.create_revision(
            _extension_request(
                fixture,
                str(uuid4()),
                ExtensionOptions(
                    predecessors=(fixture.base_revision_id,),
                    statement="An ordinary successor that touches no cycle.",
                ),
            )
        )
    assert members == tuple(sorted((first_id, second_id)))
    assert admitted.state == "created"


def test_lineage_guard_refuses_a_candidate_descending_from_a_stored_cycle(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """A candidate below a stored cycle is refused, naming the cycle, with no row written.

    The cyclic state is written outside the operation first, so the candidate's own predecessor
    set is acyclic and it is the *reach* of the rule that refuses it. The guard runs before the
    candidate's insert, which is what the unchanged row counts prove. The message must describe
    that branch: nothing points at this candidate, so it is not reachable from itself and the
    refusal may not say that it is.
    """

    closer_id, candidate_id = str(uuid4()), str(uuid4())
    with fixture.reopen() as store:
        _disable_immutability_triggers(store)
        _write_raw_revision_row(store, fixture, closer_id, "The revision that reaches back.")
        _write_raw_edge(store, fixture, closer_id, fixture.left_revision_id)
        _write_raw_edge(store, fixture, fixture.left_revision_id, closer_id)
        revisions_before = _revision_count(store)
        edges_before = _edge_count(store)
        refused = store.create_revision(
            _extension_request(
                fixture,
                candidate_id,
                ExtensionOptions(
                    predecessors=(closer_id,),
                    statement="A revision whose predecessor sits beneath a stored cycle.",
                ),
            )
        )
        revisions_after = _revision_count(store)
        edges_after = _edge_count(store)
        candidate_stored = store.get_revision(candidate_id)
    assert refused.state == "refused"
    assert refused.refusal is not None
    assert refused.refusal.code == "lineage_cycle"
    assert refused.refusal.record_id == candidate_id
    assert refused.refusal.observed == ", ".join(sorted((fixture.left_revision_id, closer_id)))
    assert candidate_stored is None
    assert revisions_after == revisions_before
    assert edges_after == edges_before
    # The message must be true for THIS branch: the candidate is below the cycle, not on it.
    assert "reachable from itself" not in refused.refusal.detail
    assert "self" not in refused.refusal.detail.lower()
    assert "descend" in refused.refusal.detail.lower()
    assert "ancestor cycle" in refused.refusal.next_action
    assert "acyclic" in refused.refusal.next_action
    assert "not itself on that cycle" in refused.refusal.next_action


def test_lineage_cycle_refusal_describes_each_branch_honestly(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """Both branches of the rule are worded for the branch they describe.

    Only the descending branch is reachable through ``create_revision``: a revision is on a cycle
    at insert time only if a stored edge already points at it, and the composite foreign key
    refuses an edge whose parent does not exist yet (proved below). The on-cycle wording is
    therefore exercised through the factory that emits it, and the descending wording is exercised
    end to end above.
    """

    ancestor_id, peer_id, candidate_id = str(uuid4()), str(uuid4()), str(uuid4())
    with fixture.reopen() as store:
        _disable_immutability_triggers(store)
        _write_raw_cycle(store, fixture, ancestor_id, peer_id)
        ghost = str(uuid4())
        with pytest.raises(apsw.ConstraintError, match="FOREIGN KEY"):
            store.connection.execute(
                "INSERT INTO invariant_predecessor VALUES (?, ?, ?, ?)",
                (fixture.repository_id, fixture.invariant_id, ancestor_id, ghost),
            )
        below = store.create_revision(
            _extension_request(
                fixture,
                candidate_id,
                ExtensionOptions(
                    predecessors=(ancestor_id,),
                    statement="A revision below a stored cycle.",
                ),
            )
        )
    assert below.refusal is not None
    assert below.refusal.code == "lineage_cycle"
    assert "reachable from itself" not in below.refusal.detail
    on_cycle = lineage_cycle_refusal(ancestor_id, (ancestor_id,), candidate_on_cycle=True)
    descending = lineage_cycle_refusal(ancestor_id, (ancestor_id,), candidate_on_cycle=False)
    assert "reachable from itself" in on_cycle.detail
    assert "reachable from itself" not in descending.detail
    assert on_cycle.next_action != descending.next_action
    assert "ancestor cycle" not in on_cycle.next_action
    assert "ancestor cycle" in descending.next_action
    assert "acyclic" in on_cycle.next_action and "acyclic" in descending.next_action
    assert ancestor_id in on_cycle.next_action


def test_lineage_guard_fires_before_the_candidate_insert(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """The guard is evaluated before the insert, so a refusal leaves both tables untouched.

    The candidate's own requested edge is the one that would close the loop, so a guard that ran
    after the insert would have to undo a written row to reach the same state.
    """

    closer_id, candidate_id = str(uuid4()), str(uuid4())
    with fixture.reopen() as store:
        _disable_immutability_triggers(store)
        _write_raw_revision_row(store, fixture, closer_id, "The revision that reaches back.")
        _write_raw_edge(store, fixture, closer_id, fixture.left_revision_id)
        _write_raw_edge(store, fixture, fixture.left_revision_id, closer_id)
        revisions_before = _revision_count(store)
        edges_before = _edge_count(store)
        refused = store.create_revision(
            _extension_request(
                fixture,
                candidate_id,
                ExtensionOptions(
                    predecessors=(closer_id,),
                    statement="The candidate that would descend from the loop.",
                ),
            )
        )
        revisions_after = _revision_count(store)
        edges_after = _edge_count(store)
    assert refused.state == "refused"
    assert refused.refusal is not None
    assert refused.refusal.code == "lineage_cycle"
    assert revisions_after == revisions_before
    assert edges_after == edges_before


def test_unknown_invariant_refuses_the_revision(fixture: BranchingKnowledgeFixture) -> None:
    """A revision of an invariant that was never created refuses as a typed outcome."""

    with fixture.reopen() as store:
        result = store.create_revision(
            _extension_request(
                fixture,
                str(uuid4()),
                ExtensionOptions(
                    invariant_id=str(uuid4()),
                    statement="A revision of an undeclared invariant.",
                ),
            )
        )
    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "unknown_invariant"


def test_other_repository_namespace_is_refused(fixture: BranchingKnowledgeFixture) -> None:
    """A request addressed to another namespace refuses before any DML."""

    foreign = str(uuid4())
    request = _extension_request(fixture, str(uuid4())).model_copy(
        update={"repository_id": foreign}
    )
    with fixture.reopen() as store:
        result = store.create_revision(request)
        invariant = store.create_invariant(
            InvariantRequest(
                repository_id=foreign,
                invariant_id=str(uuid4()),
                display_label="an invariant in another namespace",
                provenance=make_authorship(),
            )
        )
        repository = store.create_repository(
            RepositoryIdentity(repository_id=foreign, authority_home="elsewhere")
        )
        stored = store.get_repository()
    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "unauthorized_scope"
    assert result.refusal.expected == fixture.repository_id
    assert result.refusal.observed == foreign
    assert invariant.state == "refused"
    assert invariant.refusal is not None
    assert invariant.refusal.code == "unauthorized_scope"
    assert repository.state == "refused"
    assert repository.refusal is not None
    assert repository.refusal.code == "unauthorized_scope"
    assert stored is not None and stored.repository_id == fixture.repository_id


def test_stored_revision_rows_refuse_update_and_delete(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """The database itself refuses an in-place rewrite from any code path."""

    with fixture.reopen() as store:
        before = store.get_revision(fixture.left_revision_id)
        with pytest.raises(apsw.ConstraintError, match="immutable_revision"):
            store.connection.execute(
                "UPDATE invariant_revision SET statement = ? WHERE revision_id = ?",
                ("A rewrite attempted behind the identity.", fixture.left_revision_id),
            )
        with pytest.raises(apsw.ConstraintError, match="immutable_revision"):
            store.connection.execute(
                "DELETE FROM invariant_revision WHERE revision_id = ?",
                (fixture.left_revision_id,),
            )
        with pytest.raises(apsw.ConstraintError, match="immutable_revision"):
            store.connection.execute(
                "UPDATE invariant_predecessor SET parent_revision_id = ? "
                "WHERE child_revision_id = ?",
                (fixture.right_revision_id, fixture.left_revision_id),
            )
        after = store.get_revision(fixture.left_revision_id)
    assert before is not None and after is not None
    assert after == before
    assert after.revision.statement == BRANCH_A_STATEMENT


def test_payload_digest_seals_more_than_the_statement(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """A stored payload altered outside the operation is detected when it is read back.

    The trigger is dropped first because the point of the case is the *second* defence: even a
    database edited by something that bypassed this process must not be served as if the
    revision behind the identity were unchanged.
    """

    substitute = make_authorship(actor_ref="agent:substituted").model_dump_json()
    with fixture.reopen() as store:
        store.connection.execute("DROP TRIGGER invariant_revision_no_update")
        store.connection.execute(
            "UPDATE invariant_revision SET provenance = ? WHERE revision_id = ?",
            (substitute, fixture.left_revision_id),
        )
        with pytest.raises(KnowledgeStorageError, match="payload digest"):
            store.get_revision(fixture.left_revision_id)


def test_schema_carries_the_declared_manifest_and_generation(tmp_path: Path) -> None:
    """The created database carries the created generation's own manifest and column order.

    Re-scoped by `KS-R10` §Shipped Assertions: the created generation is the one creation declared
    (requirement 2.7), so the manifest, the column order and the ``user_version`` compared here are
    read from **that generation's own record** rather than from the build's generation-1 globals.
    The created table set is a superset check against the selected generation's tables, which is the
    stronger fact now that a generation appends tables the previous one did not declare.
    """

    declared = CURRENT_GENERATION
    path = tmp_path / "manifest.db"
    with open_knowledge_store(path, str(uuid4())) as store:
        tables = {
            str(row[0])
            for row in store.connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            )
        }
        user_version = int(next(iter(store.connection.execute("PRAGMA user_version")))[0])
        columns = {
            table: tuple(
                str(row[1]) for row in store.connection.execute(f"PRAGMA table_info({table})")
            )
            for table in declared.tables
        }
    assert set(declared.tables) <= tables
    assert user_version == declared.user_version
    assert columns == {table: tuple(declared.columns[table]) for table in declared.tables}


def test_partial_schema_is_refused_instead_of_written_through(tmp_path: Path) -> None:
    """A database missing a canonical table refuses rather than accepting writes."""

    path = tmp_path / "partial.db"
    open_knowledge_store(path, str(uuid4())).close()
    connection = apsw.Connection(str(path))
    connection.execute("DROP TABLE realization_claim")
    connection.close()
    with pytest.raises(KnowledgeStorageError, match="missing canonical table"):
        open_knowledge_store(path, str(uuid4()))


def test_dropped_immutability_trigger_is_refused(tmp_path: Path) -> None:
    """A database whose triggers were removed is not this schema and refuses to open."""

    path = tmp_path / "untriggered.db"
    open_knowledge_store(path, str(uuid4())).close()
    connection = apsw.Connection(str(path))
    connection.execute("DROP TRIGGER invariant_revision_no_delete")
    connection.close()
    with pytest.raises(KnowledgeStorageError, match="missing immutability trigger"):
        open_knowledge_store(path, str(uuid4()))


def test_application_seam_initializes_and_extends_one_namespace(tmp_path: Path) -> None:
    """The composed application calls create a namespace and then insert into it.

    This is the path a later leaf consumes, so it is exercised directly rather than only
    through the store: a seam that cannot initialize and then write is not a handoff.
    """

    repository_id = str(uuid4())
    invariant_id = str(uuid4())
    destination = admitted_knowledge_destination(
        tmp_path / "composed" / "candidate.db",
        RepositoryIdentity(repository_id=repository_id, authority_home="agents-remember"),
        write_authorship(
            actor_ref="agent:composed",
            authorization_ref="260915-KS developer kickoff ruling",
            origin_refs=("requirement:KS-R01@v1",),
        ),
    )
    initialized = initialize_knowledge_namespace(destination)
    reinitialized = initialize_knowledge_namespace(destination)
    with open_admitted_knowledge_store(destination) as store:
        invariant = store.create_invariant(
            InvariantRequest(
                repository_id=repository_id,
                invariant_id=invariant_id,
                display_label="a composed invariant",
                provenance=destination.authorship,
            )
        )
        request = admitted_revision_request(
            destination,
            RevisionDraft(
                revision_id=str(uuid4()),
                invariant_id=invariant_id,
                display_version="v1",
                statement="A statement authored through the composed seam.",
                applicability="Every admitted candidate write.",
                provenance=destination.authorship,
            ),
        )
        created = create_knowledge_revision(destination, request)
        stored = store.get_revision(created.revision_id)
    assert initialized.state == "created"
    assert invariant.state == "created"
    assert reinitialized.state == "refused"
    assert reinitialized.refusal is not None
    assert reinitialized.refusal.code == "destination_occupied"
    assert created.state == "created"
    assert stored is not None
    assert stored.revision.provenance == destination.authorship
    assert stored.revision.statement == "A statement authored through the composed seam."


def test_lower_ranked_owners_do_not_import_the_memory_domain() -> None:
    """The layer contract's direction holds for the new storage home.

    Storage ranks above the worktree and memory-quality owners, so an import from either into
    this package would be a cycle with a type annotation on it. The check is the reverse-import
    direction only; every other package may import the shared vocabulary freely.
    """

    package_root = Path(__file__).resolve().parents[1] / "src" / "agents_remember"
    offenders: list[str] = []
    for owner in ("worktrees", "memory_quality"):
        for module in sorted((package_root / owner).rglob("*.py")):
            for line in module.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith(
                    ("from agents_remember.memory ", "from agents_remember.memory.")
                ) or (stripped.startswith("import agents_remember.memory")):
                    offenders.append(f"{module.relative_to(package_root)}: {stripped}")
    assert offenders == []
